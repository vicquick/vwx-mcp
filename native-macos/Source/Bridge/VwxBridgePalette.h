//
//	VwxBridgePalette.h — VWX Bridge Native
//
//	macOS port. A modeless web palette that, while open, runs a CFRunLoop
//	heartbeat timer on the main run loop (~100 ms). The timer writes
//	ipc/native.alive, counts ipc/jobs/ in the VW-MCP plug-in folder and, when
//	jobs are queued, (A) posts a deferred notification whose OnIdle handler
//	drains read-only jobs, and (B) calls gSDK->DoMenuName("VWX Bridge Start")
//	so VW's command dispatcher runs the Python pump for writes. The palette's
//	JS vwxBridge.pump()/status() calls only report status / toggle pause.
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
		virtual void OnPaletteVisibilityChange(bool visible, IWebPaletteFrame* frame) override;

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
	// Internal pump command — invoked by the palette's timer via
	// gSDK->DoMenuName(), which routes through VW's real command dispatcher:
	// the only context where scripts may touch view state (vs.Layer, ...).
	class CExtMenuVwxPump_EventSink : public VWMenu_EventSink
	{
	public:
					CExtMenuVwxPump_EventSink(IVWUnknown* parent);
		virtual		~CExtMenuVwxPump_EventSink();

		virtual void DoInterface();
	};

	class CExtMenuVwxPump : public VWExtensionMenu
	{
		DEFINE_VWMenuExtension;
	public:
					CExtMenuVwxPump(CallBackPtr cbp);
		virtual		~CExtMenuVwxPump();
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
