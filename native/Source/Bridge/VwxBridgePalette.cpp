#include "StdAfx.h"

#include "VwxBridgePalette.h"

#include "Interfaces/VectorWorks/Scripting/IPythonScriptEngine.h"

#include <cstdio>
#include <ctime>

using namespace VwxBridge;

// Palette state. The pump timer only writes a heartbeat; execution runs via
// the watchdog + menu command (genuine VW command context).
static UINT_PTR      gPumpTimer   = 0;
static bool          gPaused      = false;
static int           gLastQueue   = 0;

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

// On palette close, remove the heartbeat immediately so the watchdog stops
// triggering at once (don't wait for staleness).
static void RemoveAlive(const TXString& pluginDir)
{
	if ( pluginDir.IsEmpty() )
		return;
	TXString path;
	path << pluginDir << "\\ipc\\native.alive";
	_wremove( path.GetWCharPtr() );
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
	if ( gPumpTimer == 0 )
		gPumpTimer = SetTimer( nullptr, 0, 250, PumpTimerProc );
}

void VwxBridge_StopPumpTimer()
{
	if ( gPumpTimer != 0 ) {
		KillTimer( nullptr, gPumpTimer );
		gPumpTimer = 0;
	}
	RemoveAlive( VwxPluginDir() );      // stop the watchdog immediately
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

// Timer = heartbeat + DISPATCH ONLY. It never executes scripts itself
// (WM_TIMER context hung VW inside vs.Layer — verified live). When jobs
// exist it calls gSDK->DoMenuName() on our own pump menu command, which
// routes through VW's genuine command dispatcher — the same context as a
// user menu click, where script execution is fully safe. No keystrokes,
// no focus changes, no external trigger process.
// HEARTBEAT ONLY — no dispatch from here, ever.
//
// Post-mortem of every trigger context tried on VW2026 (all verified live):
//   - CEF JS sync callback ........ vs.Layer CRASHES VW instantly
//   - WM_TIMER + ExecuteScript .... vs.Layer HANGS VW (nested loop, parked)
//   - WM_TIMER + gSDK->DoMenuName . reads work; ANY canvas mutation
//                                   (vs.Rect!) parks the frame; parked
//                                   frames detonate VW when a real command
//                                   dispatch later unwinds over them
//   - real menu click / keyboard accelerator ... full capability (drew fine)
// Conclusion: only VW's genuine command dispatch (click / accelerator) may
// run the pump. The external watchdog sends Ctrl+Shift+B (with foreground
// restore), gated by this heartbeat: palette open+unpaused = bridge on.
static void CALLBACK PumpTimerProc(HWND, UINT, UINT_PTR, DWORD)
{
	TXString pluginDir = VwxPluginDir();
	WriteAlive( pluginDir );
	gLastQueue = CountJobs( pluginDir );
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
	// STATUS ONLY. The JS callback never executes commands (that context
	// crashes VW on view-state calls). pump(true/false) toggles pause.
	if ( !args.empty() && args[0].is_boolean() )
		gPaused = args[0].get<bool>();       // pump(true) = pause, pump(false) = resume
	nlohmann::json out;
	out["jobs"]   = CountJobs( VwxPluginDir() );
	out["paused"] = gPaused;
	out["timer"]  = (gPumpTimer != 0);
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
// The pump command — runs vwx_pump.py (same file protocol as the keystroke
// bridge v4). Executed ONLY via DoMenuName from the palette timer, i.e. in
// VW's genuine command context.
static const char* kPumpScript =
	"import os, sys\n"
	"p = os.path.join(os.environ.get('APPDATA',''), 'Nemetschek', 'Vectorworks', '2026', 'Plug-ins', 'VW-MCP')\n"
	"if not os.path.isdir(p):\n"
	"    p = os.path.join(os.environ.get('APPDATA',''), 'Nemetschek', 'Vectorworks', '2026', 'Plug-ins', 'VWX-MCP')\n"
	"if p not in sys.path:\n"
	"    sys.path.insert(0, p)\n"
	"import importlib\n"
	"try:\n"
	"    import vwx_pump\n"
	"    importlib.reload(vwx_pump)\n"
	"except Exception:\n"
	"    import traceback, time\n"
	"    try:\n"
	"        with open(os.path.join(p, 'bridge.log'), 'a', encoding='utf-8') as f:\n"
	"            f.write('[%s] native pump ERROR: %s\\n' % (time.strftime('%H:%M:%S'), traceback.format_exc()))\n"
	"    except Exception:\n"
	"        pass\n";

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
	using namespace VectorWorks::Scripting;
	IPythonScriptEnginePtr engine( IID_PythonScriptEngine );
	if ( engine )
		engine->ExecuteScript( kPumpScript, NULL );
}
