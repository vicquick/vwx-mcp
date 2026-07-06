#include "StdAfx.h"

#include "VwxBridgePalette.h"

#include "Interfaces/VectorWorks/Scripting/IPythonScriptEngine.h"

#include <cstdio>
#include <ctime>

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
static UINT_PTR      gPumpTimer     = 0;
static bool          gPaused        = false;
static int           gLastQueue     = 0;
static bool          gPumping       = false;   // reentrancy guard for the drain
static bool          gNotifyInCall  = false;   // inside NotifyLayerChange => sync-delivery detector
static DWORD         gLastTrigTick  = 0;
static UINT          gPumpCmdId     = 0;        // WM_COMMAND id of our pump menu item (0 = none)
static HWND          gVwCmdWnd      = nullptr;  // the VW window that owns that menu
static HWND          gVwMainWnd     = nullptr;
static int           gDispatchCount = 0;        // times DoInterface actually ran (trigger proof)
static const StatusData kVwxMagic   = 0x56575850;   // 'VWXP' — filters our own notifications

// --------------------------------------------------------------------------------------------------------
// Helpers: VW-MCP plugin folder (job queue home) + job counting via Win32.

static TXString VwxPluginDir()
{
	const char* appdata = getenv("APPDATA");
	if ( appdata == nullptr )
		return "";
	for ( const char* name : { "VW-MCP", "VWX-MCP" } ) {
		TXString dir;
		dir << appdata << "\\Nemetschek\\Vectorworks\\2026\\Plug-ins\\" << name;
		DWORD attrs = GetFileAttributesW( dir.GetWCharPtr() );
		if ( attrs != INVALID_FILE_ATTRIBUTES && (attrs & FILE_ATTRIBUTE_DIRECTORY) )
			return dir;
	}
	return "";
}

static int CountJobs(const TXString& pluginDir)
{
	if ( pluginDir.IsEmpty() )
		return -1;
	TXString pattern;
	pattern << pluginDir << "\\ipc\\jobs\\*.json";
	WIN32_FIND_DATAW findData;
	HANDLE h = FindFirstFileW( pattern.GetWCharPtr(), &findData );
	if ( h == INVALID_HANDLE_VALUE )
		return 0;
	int n = 0;
	do {
		if ( !(findData.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) )
			n++;
	} while ( FindNextFileW( h, &findData ) );
	FindClose( h );
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
	path << pluginDir << "\\ipc\\native.alive";
	FILE* f = _wfopen( path.GetWCharPtr(), L"w" );
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
	path << pluginDir << "\\ipc\\native.alive";
	_wremove( path.GetWCharPtr() );
}

// Diagnostic trace into bridge.log (shared with the Python pump).
static void LogLine(const char* msg)
{
	TXString pluginDir = VwxPluginDir();
	if ( pluginDir.IsEmpty() )
		return;
	TXString path;
	path << pluginDir << "\\bridge.log";
	FILE* f = _wfopen( path.GetWCharPtr(), L"a" );
	if ( f ) {
		time_t t = time(nullptr);
		struct tm tmv;
		localtime_s( &tmv, &t );
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
	"p = os.path.join(os.environ.get('APPDATA',''), 'Nemetschek', 'Vectorworks', '2026', 'Plug-ins', 'VW-MCP')\n"
	"if not os.path.isdir(p):\n"
	"    p = os.path.join(os.environ.get('APPDATA',''), 'Nemetschek', 'Vectorworks', '2026', 'Plug-ins', 'VWX-MCP')\n"
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

// Trigger B discovery: find the WM_COMMAND id of our pump menu item by walking
// EVERY VW top-level window's menu bar (VW may hang the menu on a frame window
// that isn't the one with the longest title).
static UINT FindMenuItemIdRecursive(HMENU menu, const wchar_t* wanted)
{
	int n = GetMenuItemCount( menu );
	for ( int i = 0; i < n; i++ ) {
		wchar_t buf[256] = { 0 };
		MENUITEMINFOW mii = { 0 };
		mii.cbSize     = sizeof(mii);
		mii.fMask      = MIIM_STRING | MIIM_ID | MIIM_SUBMENU;
		mii.dwTypeData = buf;
		mii.cch        = 255;
		if ( !GetMenuItemInfoW( menu, i, TRUE, &mii ) )
			continue;
		if ( mii.hSubMenu ) {
			UINT id = FindMenuItemIdRecursive( mii.hSubMenu, wanted );
			if ( id != 0 )
				return id;
		}
		else {
			wchar_t* tab = wcschr( buf, L'\t' );     // strip "\tCtrl+Umschalt+B"
			if ( tab ) *tab = 0;
			if ( wcscmp( buf, wanted ) == 0 )
				return mii.wID;
		}
	}
	return 0;
}

static void TryFindPumpMenuCommandId()
{
	struct Ctx { DWORD pid; const wchar_t* wanted; HWND wnd; UINT id; int menus; HWND main; int bestLen; }
		ctx = { GetCurrentProcessId(), nullptr, nullptr, 0, 0, nullptr, -1 };
	TXString title = TXResStr( "ExtMenuVwxPump", "menu_title" );
	ctx.wanted = title.GetWCharPtr();
	EnumWindows( [](HWND h, LPARAM lp) -> BOOL {
		Ctx* c = (Ctx*) lp;
		DWORD p = 0;
		GetWindowThreadProcessId( h, &p );
		if ( p != c->pid )
			return TRUE;
		if ( IsWindowVisible( h ) ) {
			wchar_t cls[64]; GetClassNameW( h, cls, 64 );
			wchar_t ttl[256]; int len = GetWindowTextW( h, ttl, 256 );
			if ( wcscmp( cls, L"#32770" ) != 0 && len > c->bestLen ) { c->main = h; c->bestLen = len; }
		}
		HMENU bar = GetMenu( h );
		if ( bar != nullptr ) {
			c->menus++;
			UINT id = FindMenuItemIdRecursive( bar, c->wanted );
			if ( id != 0 && c->id == 0 ) { c->id = id; c->wnd = h; }
		}
		return TRUE;
	}, (LPARAM) &ctx );
	gVwMainWnd = ctx.main;
	gPumpCmdId = ctx.id;
	gVwCmdWnd  = ctx.wnd;
	char msg[160];
	if ( gPumpCmdId != 0 )
		sprintf_s( msg, "pump menu cmd id=%u found on a VW window — WM_COMMAND post ENABLED (background writes)", gPumpCmdId );
	else
		sprintf_s( msg, "no HMENU carries the pump item (%d window menu(s) scanned) — background writes need focus", ctx.menus );
	LogLine( msg );
}

// Foreground keystroke (Ctrl+Shift+B). Only used when a VW window is already
// the foreground app — no foreground-steal, so Win11 permits it. Reaches the
// accelerator -> DoInterface.
static bool VwIsForeground()
{
	HWND fg = GetForegroundWindow();
	if ( fg == nullptr )
		return false;
	DWORD p = 0;
	GetWindowThreadProcessId( fg, &p );
	return p == GetCurrentProcessId();
}

static void SendForegroundHotkey()
{
	keybd_event( VK_CONTROL, 0, 0, 0 );
	keybd_event( VK_SHIFT,   0, 0, 0 );
	keybd_event( 0x42,       0, 0, 0 );          // 'B'
	keybd_event( 0x42,       0, KEYEVENTF_KEYUP, 0 );
	keybd_event( VK_SHIFT,   0, KEYEVENTF_KEYUP, 0 );
	keybd_event( VK_CONTROL, 0, KEYEVENTF_KEYUP, 0 );
}

// Fire triggers. Called from the heartbeat timer when jobs are queued.
static bool ModalDialogOpen();
static void TriggerPump()
{
	DWORD now = GetTickCount();
	if ( now - gLastTrigTick < 400 )
		return;
	if ( ModalDialogOpen() )
		return;                                  // a real modal is up — hold
	gLastTrigTick = now;

	// A) Read-only drains in the background via the deferred notification.
	gNotifyInCall = true;
	gSDK->NotifyLayerChange( kVwxMagic );
	gNotifyInCall = false;

	// B) Writes need the 'VWX Bridge Start' Python menu command (the only
	//    proven mutation context). Reach its Ctrl+Shift+B accelerator with a
	//    keystroke — but only when VW is already the foreground app (Win11
	//    forbids background keystroke injection; jobs stay queued until the
	//    user focuses VW). No crash risk either way: pump_all never runs in
	//    an unsafe context.
	if ( VwIsForeground() )
		SendForegroundHotkey();
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

static void CALLBACK PumpTimerProc(HWND, UINT, UINT_PTR, DWORD);

void VwxBridge_StartPumpTimer()
{
	if ( gPumpTimer == 0 ) {
		gPumpTimer = SetTimer( nullptr, 0, 250, PumpTimerProc );
		gSDK->RegisterNotificationProcedure( VwxNotifyProc, kNotifyLayerChange );
		TryFindPumpMenuCommandId();
		LogLine( "bridge on (palette open)" );
	}
}

void VwxBridge_StopPumpTimer()
{
	if ( gPumpTimer != 0 ) {
		KillTimer( nullptr, gPumpTimer );
		gPumpTimer = 0;
		gSDK->UnregisterNotificationProcedure( VwxNotifyProc, kNotifyLayerChange );
		LogLine( "bridge off (palette closed)" );
	}
	RemoveAlive( VwxPluginDir() );      // external status tooling sees off at once
}

static bool ModalDialogOpen()
{
	// Never dispatch the pump while a dialog-class window is up (message box,
	// modal dialog) — the command couldn't run and might land in the dialog.
	struct Ctx { DWORD pid; bool found; } ctx = { GetCurrentProcessId(), false };
	EnumWindows( [](HWND h, LPARAM lp) -> BOOL {
		Ctx* c = (Ctx*) lp;
		DWORD p = 0;
		GetWindowThreadProcessId( h, &p );
		if ( p == c->pid && IsWindowVisible( h ) ) {
			wchar_t cls[64];
			GetClassNameW( h, cls, 64 );
			if ( wcscmp( cls, L"#32770" ) == 0 ) { c->found = true; return FALSE; }
		}
		return TRUE;
	}, (LPARAM) &ctx );
	return ctx.found;
}

// Timer = heartbeat + TRIGGER only. It never executes a script itself
// (WM_TIMER is NOT command context — mutations park it, verified live).
// TriggerPump() posts a deferred notification / WM_COMMAND; the actual drain
// runs later, at the top of VW's message loop.
static void CALLBACK PumpTimerProc(HWND, UINT, UINT_PTR, DWORD)
{
	TXString pluginDir = VwxPluginDir();
	WriteAlive( pluginDir );
	gLastQueue = CountJobs( pluginDir );
	if ( gLastQueue > 0 && !gPaused && !gPumping )
		TriggerPump();
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
	out["timer"]    = (gPumpTimer != 0);
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
