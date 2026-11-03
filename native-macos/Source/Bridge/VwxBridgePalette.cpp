#include "StdAfx.h"

#include "VwxBridgePalette.h"

#include "Interfaces/VectorWorks/Scripting/IPythonScriptEngine.h"

#include <cstdio>
#include <ctime>
#include <dirent.h>
#include <unistd.h>
#include <sys/stat.h>
#include <cstdint>
#include <CoreFoundation/CoreFoundation.h>

using namespace VwxBridge;

// Palette state.
//
// v11 — CONTEXT-SPLIT, CRASH-PROOF BY CONSTRUCTION.
//
// Definitive VW2026 context map (6 live tests):
//   - CEF JS sync callback ... read Python OK; doc mutation CRASHES.
//   - OnIdle notification .... read Python OK; opening a dialog CRASHES.
//   - genuine dispatch ....... full capability (the pump menu command's
//     DoInterface, reached by a real click / accelerator / posted WM_COMMAND).
//
// Rule enforced here: MUTATIONS RUN ONLY IN DoInterface. Background contexts
// drain read-only jobs (pump_readonly) and never touch the document. So a
// mutation that cannot reach DoInterface just stays queued (visible timeout) —
// it is never executed unsafely and CANNOT crash VW.
//
// Triggers fired from the heartbeat timer when jobs are queued:
//   A. NotifyLayerChange(magic) -> our StatusProc runs pump_readonly() from
//      OnIdle = true background reads (proven live: ping/list/get, unfocused).
//   B. PostMessage(WM_COMMAND, pumpCmdId) -> genuine dispatch -> pump_all().
//      pumpCmdId is discovered by walking EVERY VW top-level window's menu.
//      This is the one background path that can also mutate; if VW's custom
//      menubar exposes no HMENU/id it is simply unavailable (logged).
//   C. Foreground keystroke (Ctrl+Shift+B) when VW is already the foreground
//      app — reaches the accelerator -> DoInterface. Not background, but
//      Win11 permits it since no foreground-steal is needed.
static CFRunLoopTimerRef gPumpTimer     = nullptr;
static bool          gPaused        = false;
static int           gLastQueue     = 0;
static bool          gPumping       = false;   // reentrancy guard for the drain
static bool          gNotifyInCall  = false;   // inside NotifyLayerChange => sync-delivery detector
static double        gLastTrigTick  = 0.0;   // ms since plugin load (see NowMs)
static uint32_t      gPumpCmdId     = 0;        // always 0 on macOS v1 (background WM_COMMAND write path not ported)
static int           gDispatchCount = 0;        // times DoInterface actually ran (trigger proof)
static bool          gKeyStateDirty = false;    // background-hotkey key state needs restoring
static BYTE          gSavedKeyState[256] = {0};
static const StatusData kVwxMagic   = 0x56575850;   // 'VWXP' — filters our own notifications

// --------------------------------------------------------------------------------------------------------
// Helpers: VW-MCP plugin folder (job queue home) + job counting via POSIX.

static TXString VwxPluginDir()
{
	const char* home = getenv("HOME");
	if ( home == nullptr )
		return "";
	for ( const char* name : { "VW-MCP", "VWX-MCP" } ) {
		TXString dir;
		dir << home << "/Library/Application Support/Vectorworks/2026/Plug-ins/" << name;
		struct stat sb;
		if ( stat( dir.GetCharPtr(), &sb ) == 0 && S_ISDIR(sb.st_mode) )
			return dir;
	}
	return "";
}

static int CountJobs(const TXString& pluginDir)
{
	if ( pluginDir.IsEmpty() )
		return -1;
	TXString jobsDir;
	jobsDir << pluginDir << "/ipc/jobs";
	DIR* dir = opendir( jobsDir.GetCharPtr() );
	if ( dir == nullptr )
		return 0;
	int n = 0;
	struct dirent* entry;
	while ( (entry = readdir(dir)) != nullptr ) {
		const char* name = entry->d_name;
		size_t len = strlen(name);
		if ( len > 5 && strcmp( name + len - 5, ".json" ) == 0 )
			n++;
	}
	closedir( dir );
	return n;
}

// Heartbeat for the watchdog: while ipc/native.alive is fresh, the watchdog
// suppresses its fallback keystroke trigger — the palette drains jobs itself.
// native.alive = "<epoch> <paused 0|1>". The watchdog triggers the pump only
// when this file is fresh (palette open) AND paused==0.
static void WriteAlive(const TXString& pluginDir)
{
	if ( pluginDir.IsEmpty() )
		return;
	TXString path;
	path << pluginDir << "/ipc/native.alive";
	FILE* f = fopen( path.GetCharPtr(), "w" );
	if ( f ) {
		fprintf( f, "%lld %d", (long long) time(nullptr), gPaused ? 1 : 0 );
		fclose( f );
	}
}

// On palette close, remove the heartbeat immediately so external status
// tooling sees the bridge as off at once (don't wait for staleness).
static void RemoveAlive(const TXString& pluginDir)
{
	if ( pluginDir.IsEmpty() )
		return;
	TXString path;
	path << pluginDir << "/ipc/native.alive";
	remove( path.GetCharPtr() );
}

// Diagnostic trace into bridge.log (shared with the Python pump).
static void LogLine(const char* msg)
{
	TXString pluginDir = VwxPluginDir();
	if ( pluginDir.IsEmpty() )
		return;
	TXString path;
	path << pluginDir << "/bridge.log";
	FILE* f = fopen( path.GetCharPtr(), "a" );
	if ( f ) {
		time_t t = time(nullptr);
		struct tm tmv;
		localtime_r( &t, &tmv );
		fprintf( f, "[%02d:%02d:%02d] native: %s\n", tmv.tm_hour, tmv.tm_min, tmv.tm_sec, msg );
		fclose( f );
	}
}

// --------------------------------------------------------------------------------------------------------
// Two pump scripts. Both import vwx_pump (hot-reloaded) and call one entry
// point: pump_readonly() drains only read-only jobs (safe anywhere);
// pump_all() drains everything (genuine dispatch only).
static const char* kPumpReadonlyScript =
	"import os, sys, importlib\n"
	"p = os.path.join(os.environ.get('HOME',''), 'Library', 'Application Support', 'Vectorworks', '2026', 'Plug-ins', 'VW-MCP')\n"
	"if not os.path.isdir(p):\n"
	"    p = os.path.join(os.environ.get('HOME',''), 'Library', 'Application Support', 'Vectorworks', '2026', 'Plug-ins', 'VWX-MCP')\n"
	"if p not in sys.path:\n"
	"    sys.path.insert(0, p)\n"
	"try:\n"
	"    import vwx_pump\n"
	"    importlib.reload(vwx_pump)\n"
	"    vwx_pump.pump_readonly()\n"
	"except Exception:\n"
	"    import traceback, time\n"
	"    try:\n"
	"        with open(os.path.join(p, 'bridge.log'), 'a', encoding='utf-8') as f:\n"
	"            f.write('[%s] native pump_readonly ERROR: %s\\n' % (time.strftime('%H:%M:%S'), traceback.format_exc()))\n"
	"    except Exception:\n"
	"        pass\n";

static void RunScript(const char* script)
{
	if ( gPumping )
		return;
	gPumping = true;
	using namespace VectorWorks::Scripting;
	IPythonScriptEnginePtr engine( IID_PythonScriptEngine );
	if ( engine )
		engine->ExecuteScript( script, NULL );
	gPumping = false;
}

// Read-only drain — safe in the OnIdle notification context. There is NO
// native full-drain: mutations run exclusively in the 'VWX Bridge Start'
// Python menu command (VW's own script-plugin runner wraps them correctly;
// the raw engine call from native code does not — crashed live, twice).
static void VwxRunPumpReadonly() { RunScript( kPumpReadonlyScript ); }

// --------------------------------------------------------------------------------------------------------
// Trigger A: deferred notification -> read-only drain from OnIdle.
// VW distributes notifications from OnIdle (MCNotification.h) = top-level main
// loop. pump_readonly() there is safe (no mutation, no dialog). If VW ever
// delivered this synchronously (inside our WM_TIMER frame) gNotifyInCall would
// suppress it — but even then pump_readonly can't crash (read-only only).
static void VwxNotifyProc(StatusID /*id*/, StatusData data)
{
	if ( data != kVwxMagic )
		return;                                  // a real layer change — not ours
	if ( gNotifyInCall )
		return;                                  // synchronous delivery: skip (belt-and-braces)
	VwxRunPumpReadonly();                        // background reads
}

// Stubbed out for macOS
static void TryFindPumpMenuCommandId()
{
	LogLine("menu-command-ID lookup is unused on macOS; DoMenuName handles background writes");
}

// Stubbed out for macOS
static void PostBackgroundHotkey()
{
	LogLine("background-wake-via-keystroke not implemented on macOS (v1)");
}

// Stubbed out for macOS
static void RestoreKeyState()
{
	LogLine("background-wake-via-keystroke not implemented on macOS (v1)");
}

// Stubbed out for macOS
static bool VwIsForeground()
{
	LogLine("background-wake-via-keystroke not implemented on macOS (v1)");
	return false;
}

// Stubbed out for macOS
static void SendForegroundHotkey()
{
	LogLine("background-wake-via-keystroke not implemented on macOS (v1)");
}

// Fire triggers. Called from the heartbeat timer when jobs are queued.
static bool ModalDialogOpen()
{
	return false;
}

static double NowMs() { return CFAbsoluteTimeGetCurrent() * 1000.0; }

static void TriggerPump()
{
	double now = NowMs();
	if ( now - gLastTrigTick < 120 )        // snappier writes (was 400ms)
		return;
	if ( ModalDialogOpen() )
		return;                                  // a real modal is up — hold
	gLastTrigTick = now;

	// A) Read-only drains in the background via the deferred notification.
	gNotifyInCall = true;
	gSDK->NotifyLayerChange( kVwxMagic );
	gNotifyInCall = false;

	// B) Writes: macOS has no keystroke-injection equivalent to the Windows
	//    accelerator trick (that mechanism is stubbed, see PostBackgroundHotkey
	//    et al above). Instead, call the SDK's own DoMenuName() to invoke the
	//    'VWX Bridge Start' menu command directly through VW's real command
	//    dispatcher -- the same genuine-dispatch context a user click or
	//    keyboard shortcut would reach, focus-independent, no Accessibility
	//    permission needed.
	gSDK->DoMenuName( "VWX Bridge Start", 0 );
}

// --------------------------------------------------------------------------------------------------------
// Palette heartbeat timer.
//
// HARD-WON LESSON: scripts / view-state calls (vs.Layer, …) must NOT run from
// the web palette's JS sync callback NOR from a WM_TIMER — both are outside
// VW's command frame. The JS context CRASHED VW; the WM_TIMER context HUNG VW
// inside vs.Layer (both verified live). The only safe place to drive the app
// is VW's genuine command dispatch = a menu command. So this timer does the
// bare minimum (heartbeat + queue count) and the watchdog triggers the
// "VWX Bridge Start" menu command to actually pump.

static void PumpTimerProc(CFRunLoopTimerRef, void*)
{
	TXString pluginDir = VwxPluginDir();
	WriteAlive( pluginDir );
	gLastQueue = CountJobs( pluginDir );
	if ( gLastQueue > 0 && !gPaused && !gPumping )
		TriggerPump();
}

void VwxBridge_StartPumpTimer()
{
	if ( gPumpTimer == nullptr ) {
		CFRunLoopTimerContext ctx = { 0 };
		gPumpTimer = CFRunLoopTimerCreate(kCFAllocatorDefault, CFAbsoluteTimeGetCurrent() + 0.1, 0.1, 0, 0, PumpTimerProc, &ctx);
		CFRunLoopAddTimer(CFRunLoopGetMain(), gPumpTimer, kCFRunLoopCommonModes);
		gSDK->RegisterNotificationProcedure( VwxNotifyProc, kNotifyLayerChange );
		TryFindPumpMenuCommandId();
		LogLine( "bridge on (palette open)" );
	}
}

void VwxBridge_StopPumpTimer()
{
	if ( gPumpTimer != nullptr ) {
		CFRunLoopTimerInvalidate(gPumpTimer);
		CFRelease(gPumpTimer);
		gPumpTimer = nullptr;
		gSDK->UnregisterNotificationProcedure( VwxNotifyProc, kNotifyLayerChange );
		LogLine( "bridge off (palette closed)" );
	}
	RemoveAlive( VwxPluginDir() );      // external status tooling sees off at once
}

// --------------------------------------------------------------------------------------------------------
extern const char * DefaultPluginVWRIdentifier();

// NOTE: dispatch-map keys are the bare FUNCTION names — OnFunction receives
// (objName='vwxBridge', functionName='pump'). Registration below uses the
// full dotted name; the map must not.
BEGIN_WebPalette_DISPATCH_MAP(CVwxJSProvider)
ADD_WebPalette_FUNCTION( "pump",   OnPump )
ADD_WebPalette_FUNCTION( "status", OnStatus )
END_WebPalette_DISPATCH_MAP

CVwxJSProvider::CVwxJSProvider( IVWUnknown* parent )
	: VWExtensionPaletteJSProvider( parent )
{
}

CVwxJSProvider::~CVwxJSProvider()
{
}

void CVwxJSProvider::OnInit(IInitContext* context)
{
	fWebFrame = context->GetWebFrame();

	// creates the window.vwxBridge integrator object on the JS side
	context->AddReourceAccessFunction( "vwxBridge", DefaultPluginVWRIdentifier() );

	// Sync = executed on the Vectorworks main thread (safe to call the SDK /
	// the Python engine). The JS side awaits the returned promise.
	context->AddFunctionPromiseSync( "vwxBridge.pump" );
	context->AddFunctionPromiseSync( "vwxBridge.status" );

	// Palette page loaded => palette is visible => bridge on.
	VwxBridge_StartPumpTimer();
}

void CVwxJSProvider::OnPaletteVisibilityChange(bool visible, IWebPaletteFrame* frame)
{
	// Palette open = bridge alive. Palette closed = bridge off. The Pause
	// button in the palette pauses without closing.
	if ( visible )
		VwxBridge_StartPumpTimer();
	else
		VwxBridge_StopPumpTimer();
}

void CVwxJSProvider::OnPump(const TXString& objName, const TXString& functionName, const std::vector<nlohmann::json>& args, VectorWorks::UI::IJSFunctionCallbackContext* context)
{
	// STATUS ONLY — document mutation from the CEF sync callback CRASHES VW
	// (verified live 2026-07-06 with plain vs.Rect; the SDK's own
	// kNotifyGenericWebPalette exists because work must happen "outside the
	// SyncProxy callback"). The drain runs via TriggerPump's deferred paths.
	// pump(true/false) toggles pause without closing the palette.
	if ( !args.empty() && args[0].is_boolean() )
		gPaused = args[0].get<bool>();       // pump(true) = pause, pump(false) = resume
	nlohmann::json out;
	out["jobs"]     = CountJobs( VwxPluginDir() );
	out["paused"]   = gPaused;
	out["timer"]    = (gPumpTimer != nullptr);
	out["cmdId"]    = (unsigned) gPumpCmdId;   // 0 = no background-write path
	out["dispatch"] = gDispatchCount;          // times DoInterface ran (trigger proof)
	out["pumping"]  = gPumping;
	context->Resolve( out );
}

void CVwxJSProvider::OnStatus(const TXString& objName, const TXString& functionName, const std::vector<nlohmann::json>& args, VectorWorks::UI::IJSFunctionCallbackContext* context)
{
	nlohmann::json out;
	TXString pluginDir = VwxPluginDir();
	out["pluginDir"] = (const char*) pluginDir;
	out["pluginDirFound"] = !pluginDir.IsEmpty();
	out["jobs"] = CountJobs( pluginDir );
	context->Resolve( out );
}

// --------------------------------------------------------------------------------------------------------
CExtVwxBridgePalette::CExtVwxBridgePalette(CallBackPtr)
{
}

CExtVwxBridgePalette::~CExtVwxBridgePalette()
{
}

void CExtVwxBridgePalette::DefineSinks()
{
	this->DefineSink<CVwxJSProvider>( IID_WebJavaScriptProvider );
}

TXString CExtVwxBridgePalette::GetTitle()
{
	return TXResStr("ExtVwxBridgePalette", "paletteName");
}

bool CExtVwxBridgePalette::GetInitialSize(ViewCoord& outCX, ViewCoord& outCY)
{
	outCX = 340;
	outCY = 220;
	return true;
}

bool CExtVwxBridgePalette::GetMinimalSize(ViewCoord& outCX, ViewCoord& outCY)
{
	outCX = 240;
	outCY = 140;
	return true;
}

TXString CExtVwxBridgePalette::GetInitialURL()
{
	const TXString	htmlFolderName	= "html";
	const TXString	htmlFile		= "index.html";
	return VWFC::PluginSupport::GetStandardURL( htmlFolderName, htmlFile );
}

// --------------------------------------------------------------------------------------------------------
// {28AEC847-912F-4C03-9982-F0E7F1AB78F3}
IMPLEMENT_VWPaletteExtension(
	/*Extension class*/	CExtVwxBridgePalette,
	/*Universal name*/	"VwxBridge",
	/*Version*/			1,
	/*UUID*/			0x28aec847, 0x912f, 0x4c03, 0x99, 0x82, 0xf0, 0xe7, 0xf1, 0xab, 0x78, 0xf3 );

// --------------------------------------------------------------------------------------------------------
static SMenuDef		gMenuDef = {
	/*Needs*/				EMenuEnableFlags::None,
	/*NeedsNot*/			EMenuEnableFlags::None,
	/*Title*/				{"ExtMenuShowVwxBridge", "menu_title"},
	/*Category*/			{"ExtMenuShowVwxBridge", "menu_category"},
	/*HelpText*/			{"ExtMenuShowVwxBridge", "menu_helptext"},
	/*VersionCreated*/		30,
	/*VersoinModified*/		0,
	/*VersoinRetired*/		0,
	/*OverrideHelpID*/		" "
};

// --------------------------------------------------------------------------------------------------------
// {6DB485A0-F1BC-4299-A415-7EF65C373C27}
IMPLEMENT_VWMenuExtension(
	/*Extension class*/	CExtMenuShowVwxBridge,
	/*Event sink*/		CExtMenuShowVwxBridge_EventSink,
	/*Universal name*/	"ExtMenuShowVwxBridge",
	/*Version*/			1,
	/*UUID*/			0x6db485a0, 0xf1bc, 0x4299, 0xa4, 0x15, 0x7e, 0xf6, 0x5c, 0x37, 0x3c, 0x27 );

// --------------------------------------------------------------------------------------------------------
CExtMenuShowVwxBridge::CExtMenuShowVwxBridge(CallBackPtr cbp)
	: VWExtensionMenu( cbp, gMenuDef )
{
}

CExtMenuShowVwxBridge::~CExtMenuShowVwxBridge()
{
}

// --------------------------------------------------------------------------------------------------------
CExtMenuShowVwxBridge_EventSink::CExtMenuShowVwxBridge_EventSink(IVWUnknown* parent)
	: VWMenu_EventSink( parent )
{
}

CExtMenuShowVwxBridge_EventSink::~CExtMenuShowVwxBridge_EventSink()
{
}

void CExtMenuShowVwxBridge_EventSink::DoInterface()
{
	gSDK->SetWebPaletteVisibility( CExtVwxBridgePalette::_GetIID(), true );
}

// --------------------------------------------------------------------------------------------------------
// Manual pump menu command — kept as a debug fallback (drains the queue once).
// The palette self-pumps; this is only for troubleshooting without the palette.
static SMenuDef		gPumpMenuDef = {
	/*Needs*/				EMenuEnableFlags::None,
	/*NeedsNot*/			EMenuEnableFlags::None,
	/*Title*/				{"ExtMenuVwxPump", "menu_title"},
	/*Category*/			{"ExtMenuVwxPump", "menu_category"},
	/*HelpText*/			{"ExtMenuVwxPump", "menu_helptext"},
	/*VersionCreated*/		30,
	/*VersoinModified*/		0,
	/*VersoinRetired*/		0,
	/*OverrideHelpID*/		" "
};

// {234BFD96-FDB4-4C9B-AF64-A5625EACA77B}
IMPLEMENT_VWMenuExtension(
	/*Extension class*/	CExtMenuVwxPump,
	/*Event sink*/		CExtMenuVwxPump_EventSink,
	/*Universal name*/	"ExtMenuVwxPump",
	/*Version*/			1,
	/*UUID*/			0x234bfd96, 0xfdb4, 0x4c9b, 0xaf, 0x64, 0xa5, 0x62, 0x5e, 0xac, 0xa7, 0x7b );

CExtMenuVwxPump::CExtMenuVwxPump(CallBackPtr cbp)
	: VWExtensionMenu( cbp, gPumpMenuDef )
{
}

CExtMenuVwxPump::~CExtMenuVwxPump()
{
}

CExtMenuVwxPump_EventSink::CExtMenuVwxPump_EventSink(IVWUnknown* parent)
	: VWMenu_EventSink( parent )
{
}

CExtMenuVwxPump_EventSink::~CExtMenuVwxPump_EventSink()
{
}

void CExtMenuVwxPump_EventSink::DoInterface()
{
	// DO NOT EXECUTE SCRIPTS HERE. A native menu extension's DoInterface +
	// raw IPythonScriptEngine::ExecuteScript crashed VW on document mutation
	// (verified 2026-07-06, twice: manual click v7 and accelerator v11) —
	// unlike VW's own PYTHON menu-command plugin runner, which wraps script
	// execution in a proper document context. The mutation executor is the
	// "VWX Bridge Start" Python menu command (BridgeStart_MenuCommand.py,
	// Ctrl+Shift+B); this native command remains only as a status probe.
	gDispatchCount++;
	LogLine( "native DoInterface reached — no-op (mutation executor is the "
	         "'VWX Bridge Start' Python menu command, Ctrl+Shift+B)" );
}
