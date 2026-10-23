// SPDX-License-Identifier: GPL-3.0-or-later
// Render the actual local frontend with explicitly synthetic data. No Apple or device requests.
import AppKit
import WebKit

final class Preview: NSObject, WKNavigationDelegate {
    let web = WKWebView(frame: NSRect(x: 0, y: 0, width: 1440, height: 1000))
    var window: NSWindow!
    let root = URL(fileURLWithPath: CommandLine.arguments[1])
    let output = URL(fileURLWithPath: CommandLine.arguments[2])
    func start() {
        window = NSWindow(contentRect: web.frame, styleMask: .borderless, backing: .buffered, defer: false)
        window.contentView = web
        web.navigationDelegate = self
        let frontend = root.appendingPathComponent("src/web")
        var html = try! String(contentsOf: frontend.appendingPathComponent("index.html"), encoding: .utf8)
        let style = try! String(contentsOf: frontend.appendingPathComponent("style.css"), encoding: .utf8)
        let script = try! String(contentsOf: frontend.appendingPathComponent("app.js"), encoding: .utf8)
        let mock = """
        window.fetch = async (path, options) => ({ok:true,json:async()=> path.endsWith('/status') ? {device_ready:true,jobs:[],version:'0.1.0'} : path.endsWith('/library') ? {files:[]} : path.endsWith('/actions') ? {job_id:'preview'} : path.endsWith('/heartbeat') ? {ok:true} : {id:'preview',action:'devices',status:'done',result:[],events:[]}});
        """
        html = html.replacingOccurrences(of: "<link rel=\"stylesheet\" href=\"/style.css\">", with: "<style>" + style + "</style>")
        html = html.replacingOccurrences(of: "<script src=\"/app.js\" defer></script>", with: "")
        html = html.replacingOccurrences(of: "</body>", with: "<script>" + mock + script + "</script></body>")
        web.loadHTMLString(html, baseURL: URL(string: "http://127.0.0.1"))
        DispatchQueue.main.asyncAfter(deadline: .now() + 20) { exit(2) }
    }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        DispatchQueue.main.asyncAfter(deadline: .now() + 1) {
            let js = """
            state.devices=[{udid:'DEMO-0000000000',name:'Demo iPhone',ios:'26.0'}];
            state.library=[{name:'Example Notes',bundle_id:'com.example.notes',version:'2.4.0',size:124780544,filename:'demo-notes.ipa'},{name:'Example Wallet',bundle_id:'com.example.wallet',version:'3.1.2',size:203423744,filename:'demo-wallet.ipa'}];
            state.ready=true; state.jobs=[]; state.busy=false;
            renderDevices(); renderLibrary(); view('library');
            document.querySelector('#job-banner').hidden=true;
            document.querySelector('#toast').hidden=true;
            document.querySelector('footer').lastElementChild.textContent='Демонстрационные данные · Apple не подключён';
            true;
            """
            self.web.evaluateJavaScript(js) { _, error in
                if let error = error { fputs(error.localizedDescription, stderr); exit(3) }
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
                    self.web.takeSnapshot(with: nil) { image, error in
                        guard let image = image, let data = image.tiffRepresentation,
                              let bitmap = NSBitmapImageRep(data: data),
                              let png = bitmap.representation(using: .png, properties: [:]) else { exit(4) }
                        try! png.write(to: self.output)
                        print("Preview saved: " + self.output.path)
                        NSApp.terminate(nil)
                    }
                }
            }
        }
    }
}
let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let preview = Preview()
preview.start()
app.run()
