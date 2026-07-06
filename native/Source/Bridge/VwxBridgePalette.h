//
//	VwxBridgePalette.h — VWX Bridge Native
//
//	A modeless web palette whose JS polls vwxBridge.pump() (~250ms). The pump
//	callback runs SYNC on the Vectorworks main thread (safe for the SDK): it
//	checks ipc/jobs/ in the VW-MCP plugin folder and, when jobs exist, runs
//	vwx_pump.py via IPythonScriptEngine — the same file protocol as the
//	keystroke-triggered bridge v4, minus the keystroke.
//

#pragma once

namespace VwxBridge
{
	using namespace VectorWorks::Extension;
	using namespace VWFC::PluginSupport;

	// --------------------------------------------------------------------------------------------------------
	class CVwxJSProvider : public VWExtensionPaletteJSProvider
	{
	public:
					CVwxJSProvider(IVWUnknown* parent);
		virtual		~CVwxJSProvider();

		virtual void OnInit(IInitContext* context);

		DEFINE_WebPalette_DISPATCH_MAP;

	private:
		void	OnPump  (const TXString& objName, const TXString& functionName, const std::vector<nlohmann::json>& args, VectorWorks::UI::IJSFunctionCallbackContext* context);
		void	OnStatus(const TXString& objName, const TXString& functionName, const std::vector<nlohmann::json>& args, VectorWorks::UI::IJSFunctionCallbackContext* context);
	};

	// --------------------------------------------------------------------------------------------------------
	class CExtVwxBridgePalette : public VWExtensionWebPalette
	{
		DEFINE_VWPaletteExtension;
	public:
					CExtVwxBridgePalette(CallBackPtr);
		virtual		~CExtVwxBridgePalette();

		virtual void		DefineSinks();

		virtual TXString	VCOM_CALLTYPE GetTitle();
		virtual bool		VCOM_CALLTYPE GetInitialSize(ViewCoord& outCX, ViewCoord& outCY);
		virtual bool		VCOM_CALLTYPE GetMinimalSize(ViewCoord& outCX, ViewCoord& outCY);
		virtual TXString	VCOM_CALLTYPE GetInitialURL();
	};

	// --------------------------------------------------------------------------------------------------------
	class CExtMenuShowVwxBridge_EventSink : public VWMenu_EventSink
	{
	public:
					CExtMenuShowVwxBridge_EventSink(IVWUnknown* parent);
		virtual		~CExtMenuShowVwxBridge_EventSink();

		virtual void DoInterface();
	};

	// --------------------------------------------------------------------------------------------------------
	class CExtMenuShowVwxBridge : public VWExtensionMenu
	{
		DEFINE_VWMenuExtension;
	public:
					CExtMenuShowVwxBridge(CallBackPtr cbp);
		virtual		~CExtMenuShowVwxBridge();
	};
}
