// SPDX-License-Identifier: GPL-3.0-or-later
import AppKit
import WebKit
import UniformTypeIdentifiers

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKScriptMessageHandler, WKNavigationDelegate {
    var window: NSWindow!
    var web: WKWebView!
    var server: Process?
    var localPort: Int?
    var terminating = false
    var initialOutput = Data()

    var resources: URL { Bundle.main.resourceURL! }
    var root: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("PhoneBridge", isDirectory: true)
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let menu = NSMenu()
        let applicationItem = NSMenuItem()
        let applicationMenu = NSMenu()
        applicationMenu.addItem(withTitle: "О PhoneBridge", action: #selector(about), keyEquivalent: "")
        applicationMenu.addItem(NSMenuItem.separator())
        applicationMenu.addItem(withTitle: "Завершить PhoneBridge", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        applicationItem.submenu = applicationMenu
        menu.addItem(applicationItem)
        let editItem = NSMenuItem()
        let editMenu = NSMenu(title: "Правка")
        editMenu.addItem(withTitle: "Отменить", action: Selector(("undo:")), keyEquivalent: "z")
        editMenu.addItem(withTitle: "Вырезать", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "Скопировать", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "Вставить", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "Выбрать всё", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = editMenu; menu.addItem(editItem); NSApp.mainMenu = menu

        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1160, height: 820), styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "PhoneBridge"
        window.minSize = NSSize(width: 820, height: 640)
        window.center(); window.delegate = self
        let controller = WKUserContentController()
        controller.add(self, name: "native")
        let configuration = WKWebViewConfiguration()
        configuration.userContentController = controller
        configuration.websiteDataStore = .nonPersistent()
        web = WKWebView(frame: window.contentView!.bounds, configuration: configuration)
        web.autoresizingMask = [.width, .height]
        web.navigationDelegate = self
        web.underPageBackgroundColor = NSColor(calibratedRed: 0.965, green: 0.969, blue: 0.957, alpha: 1)
        window.contentView = web
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        startServer()
    }

    func startServer() {
        do {
            try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true,
                attributes: [.posixPermissions: 0o700])
        } catch { showError("Не удалось создать папку данных. " + error.localizedDescription); return }
        #if arch(arm64)
        let architecture = "arm64"
        #else
        let architecture = "x86_64"
        #endif
        let python = resources.appendingPathComponent("runtime/" + architecture + "/bin/python3.12").path
        guard FileManager.default.isExecutableFile(atPath: python) else {
            showError("Не найден встроенный Python. Установи полную сборку PhoneBridge из DMG."); return
        }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: python)
        process.arguments = ["-u", resources.appendingPathComponent("server.py").path]
        process.currentDirectoryURL = root
        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["PYTHONNOUSERSITE"] = "1"
        environment.removeValue(forKey: "PYTHONHOME")
        environment.removeValue(forKey: "PYTHONPATH")
        environment["PHONEBRIDGE_DATA_DIR"] = root.path
        process.environment = environment
        let stdout = Pipe(), stderr = Pipe()
        process.standardOutput = stdout
        process.standardError = stderr
        // Backend errors never include credentials, but keep stderr in memory only.
        stderr.fileHandleForReading.readabilityHandler = { handle in _ = handle.availableData }
        stdout.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let bytes = handle.availableData
            guard !bytes.isEmpty else { return }
            DispatchQueue.main.async {
                guard let self = self else { return }
                self.initialOutput.append(bytes)
                guard let newline = self.initialOutput.firstIndex(of: 10) else { return }
                let firstLine = self.initialOutput.prefix(upTo: newline)
                guard let info = try? JSONSerialization.jsonObject(with: firstLine) as? [String: String],
                      let address = info["url"], let url = URL(string: address), url.host == "127.0.0.1" else { return }
                self.localPort = url.port
                stdout.fileHandleForReading.readabilityHandler = nil
                self.web.load(URLRequest(url: url))
            }
        }
        process.terminationHandler = { [weak self] _ in
            DispatchQueue.main.async {
                guard let self = self, !self.terminating else { return }
                self.showError("Локальный сервер завершился. Перезапусти приложение. Если ошибка повторится, открой инструкцию в репозитории PhoneBridge.")
            }
        }
        do { try process.run(); server = process }
        catch { showError("Не удалось запустить встроенный Python. " + error.localizedDescription) }
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.frameInfo.isMainFrame,
              message.frameInfo.securityOrigin.host == "127.0.0.1",
              message.frameInfo.securityOrigin.port == localPort,
              let body = message.body as? [String: Any], let action = body["action"] as? String else { return }
        if action == "import" {
            let panel = NSOpenPanel()
            panel.title = "Добавить установочный файл IPA"
            panel.allowedContentTypes = [UTType(filenameExtension: "ipa") ?? .data]
            panel.allowsMultipleSelection = false
            panel.canChooseDirectories = false
            panel.beginSheetModal(for: window) { [weak self] result in
                guard result == .OK, let url = panel.url,
                      let data = try? JSONSerialization.data(withJSONObject: [url.path]),
                      let argument = String(data: data, encoding: .utf8) else { return }
                self?.web.evaluateJavaScript("window.nativeImport(" + argument + "[0])")
            }
        } else if action == "library" {
            NSWorkspace.shared.open(root.appendingPathComponent("Library", isDirectory: true))
        }
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else { decisionHandler(.cancel); return }
        decisionHandler(url.scheme == "http" && url.host == "127.0.0.1" && url.port == localPort ? .allow : .cancel)
    }
    @objc func about() {
        NSApp.orderFrontStandardAboutPanel(options: [.applicationName: "PhoneBridge", .applicationVersion: "0.1.0", .credits: NSAttributedString(string: "kgpd02 · Product Builder\ngithub.com/kgpd02/phonebridge\nЛокальный архив приложений iPhone. Открытые исходники.")])
    }
    func showError(_ message: String) {
        let alert = NSAlert(); alert.messageText = "PhoneBridge"; alert.informativeText = message
        alert.addButton(withTitle: "Закрыть"); alert.runModal()
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationWillTerminate(_ notification: Notification) {
        terminating = true
        if let server = server, server.isRunning { server.terminate() }
    }
}

let application = NSApplication.shared
let delegate = AppDelegate()
application.delegate = delegate
application.setActivationPolicy(.regular)
application.run()
