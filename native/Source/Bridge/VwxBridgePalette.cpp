#include "StdAfx.h"

#include "VwxBridgePalette.h"

#include "Interfaces/VectorWorks/Scripting/IPythonScriptEngine.h"

#include <cstdio>
#include <ctime>

using namespace VwxBridge;

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
static void WriteAlive(const TXString& pluginDir)
{
	if ( pluginDir.IsEmpty() )
		return;
	TXString path;
	path << pluginDir << "\\ipc\\native.alive";
	FILE* f = _wfopen( path.GetWCharPtr(), L"w" );
	if ( f ) {
		fprintf( f, "%lld", (long long) time(nullptr) );
		fclose( f );
	}
}

// The pump script: reuse the exact same vwx_pump.py the keystroke bridge v4
// uses — one file protocol, two triggers.
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

// --------------------------------------------------------------------------------------------------------
extern const char * DefaultPluginVWRIdentifier();

BEGIN_WebPalette_DISPATCH_MAP(CVwxJSProvider)
ADD_WebPalette_FUNCTION( "vwxBridge.pump",   OnPump )
ADD_WebPalette_FUNCTION( "vwxBridge.status", OnStatus )
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
}

void CVwxJSProvider::OnPump(const TXString& objName, const TXString& functionName, const std::vector<nlohmann::json>& args, VectorWorks::UI::IJSFunctionCallbackContext* context)
{
	nlohmann::json out;
	TXString pluginDir = VwxPluginDir();
	WriteAlive( pluginDir );
	int jobs = CountJobs( pluginDir );
	out["jobs"] = jobs;
	out["pumped"] = false;
	if ( jobs > 0 ) {
		using namespace VectorWorks::Scripting;
		IPythonScriptEnginePtr engine( IID_PythonScriptEngine );
		if ( engine ) {
			engine->ExecuteScript( kPumpScript, NULL );
			out["pumped"] = true;
			out["jobsAfter"] = CountJobs( pluginDir );
		}
		else {
			out["error"] = "IPythonScriptEngine unavailable";
		}
	}
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
