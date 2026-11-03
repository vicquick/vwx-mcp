//
//	ModuleMain.cpp — VWX Bridge Native (web-palette pump)
//
//	Registers:
//	  - CExtVwxBridgePalette  : modeless web palette hosting the JS pump
//	  - CExtMenuShowVwxBridge : menu command that shows the palette
//

#include "StdAfx.h"

#include "Bridge/VwxBridgePalette.h"

const char * DefaultPluginVWRIdentifier() { return "VwxBridge"; }

//------------------------------------------------------------------
// provide SDK version for which this plugin was compiled
extern "C" Sint32 GS_EXTERNAL_ENTRY plugin_module_ver() { return SDK_VERSION; }

//------------------------------------------------------------------
extern "C" Sint32 GS_EXTERNAL_ENTRY plugin_module_main(Sint32 action, void* moduleInfo, const VWIID& iid, IVWUnknown*& inOutInterface, CallBackPtr cbp)
{
	::GS_InitializeVCOM( cbp );

	Sint32	reply	= 0L;

	using namespace VWFC::PluginSupport;

	// NOTE: no side effects here — arming anything from module registration
	// crashes VW during boot (verified live, twice). The pump timer starts
	// and stops with the palette's visibility (Bridge/VwxBridgePalette.cpp).
	REGISTER_Extension<VwxBridge::CExtVwxBridgePalette>( GROUPID_ExtensionWebPalettes, action, moduleInfo, iid, inOutInterface, cbp, reply );
	REGISTER_Extension<VwxBridge::CExtMenuShowVwxBridge>( GROUPID_ExtensionMenu, action, moduleInfo, iid, inOutInterface, cbp, reply );
	REGISTER_Extension<VwxBridge::CExtMenuVwxPump>( GROUPID_ExtensionMenu, action, moduleInfo, iid, inOutInterface, cbp, reply );

	return reply;
}
