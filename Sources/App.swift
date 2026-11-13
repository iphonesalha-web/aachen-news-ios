import SwiftUI
import WebKit

@main
struct AachenNewsApp: App {
    var body: some Scene {
        WindowGroup {
            WebHost().ignoresSafeArea()
        }
    }
}

struct WebHost: UIViewRepresentable {
    func makeUIView(context: Context) -> WKWebView {
        let wv = WKWebView(frame: .zero)
        wv.navigationDelegate = context.coordinator
        wv.backgroundColor = .white
        wv.isOpaque = false
        if let url = Bundle.main.url(forResource: "index", withExtension: "html", subdirectory: "web") {
            wv.loadFileURL(url, allowingReadAccessTo: url.deletingLastPathComponent())
        }
        return wv
    }

    func updateUIView(_ uiView: WKWebView, context: Context) {}

    func makeCoordinator() -> Coordinator { Coordinator() }

    final class Coordinator: NSObject, WKNavigationDelegate {
        // الروابط الخارجية (المصادر) تفتح في Safari
        func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                     decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
            if action.navigationType == .linkActivated,
               let url = action.request.url,
               let scheme = url.scheme?.lowercased(), scheme == "http" || scheme == "https" {
                UIApplication.shared.open(url)
                decisionHandler(.cancel)
                return
            }
            decisionHandler(.allow)
        }
    }
}
