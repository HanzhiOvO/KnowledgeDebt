import AppKit
import Darwin
import Foundation
import Security

private enum LauncherError: LocalizedError {
    case missingRuntime(String)
    case noFreePort
    case keychain(OSStatus)
    case childExited(String, Int32)

    var errorDescription: String? {
        switch self {
        case .missingRuntime(let path):
            return "应用运行组件缺失：\(path)。请重新安装 KnowledgeDebt。"
        case .noFreePort:
            return "无法分配本地端口，请关闭占用大量本地端口的程序后重试。"
        case .keychain(let status):
            return "无法访问 macOS 钥匙串（错误 \(status)），为避免明文保存密钥，应用已停止启动。"
        case .childExited(let name, let status):
            return "\(name) 异常退出（状态 \(status)）。原始数据未删除，请查看诊断日志。"
        }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem?
    private var openItem: NSMenuItem?
    private var stateItem: NSMenuItem?
    private var backend: Process?
    private var web: Process?
    private var logHandles: [FileHandle] = []
    private var lockDescriptor: Int32 = -1
    private var stopping = false
    private var webURL: URL?
    private var dataRoot: URL!
    private var runtimeState: URL!
    private var logs: URL!

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        do {
            try prepareDataLayout()
            guard acquireSingleInstanceLock() else {
                openExistingInstance()
                NSApp.terminate(nil)
                return
            }
            configureMenu()
            try startServices()
        } catch {
            presentFatal(error)
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        stopping = true
        stop(process: web)
        stop(process: backend)
        for handle in logHandles {
            try? handle.close()
        }
        if lockDescriptor >= 0 {
            flock(lockDescriptor, LOCK_UN)
            close(lockDescriptor)
        }
    }

    private func prepareDataLayout() throws {
        let applicationSupport = try FileManager.default.url(
            for: .applicationSupportDirectory,
            in: .userDomainMask,
            appropriateFor: nil,
            create: true
        )
        dataRoot = applicationSupport.appendingPathComponent("KnowledgeDebt", isDirectory: true)
        let root = dataRoot!
        let stateDirectory = root.appendingPathComponent("runtime-state", isDirectory: true)
        let logsDirectory = root.appendingPathComponent("logs", isDirectory: true)
        runtimeState = stateDirectory
        logs = logsDirectory
        for directory in [
            root,
            stateDirectory,
            logsDirectory,
            root.appendingPathComponent("backups", isDirectory: true),
            root.appendingPathComponent("models", isDirectory: true),
            root.appendingPathComponent("resources", isDirectory: true),
            root.appendingPathComponent("recording-chunks", isDirectory: true),
            root.appendingPathComponent("transcription-chunks", isDirectory: true),
            root.appendingPathComponent("secrets", isDirectory: true),
        ] {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        }
    }

    private func acquireSingleInstanceLock() -> Bool {
        let path = runtimeState.appendingPathComponent("application.lock").path
        lockDescriptor = Darwin.open(path, O_CREAT | O_RDWR, S_IRUSR | S_IWUSR)
        guard lockDescriptor >= 0 else { return false }
        return flock(lockDescriptor, LOCK_EX | LOCK_NB) == 0
    }

    private func configureMenu() {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.title = "KD"
        item.button?.toolTip = "KnowledgeDebt"
        let menu = NSMenu()
        let state = NSMenuItem(title: "正在启动本地服务…", action: nil, keyEquivalent: "")
        state.isEnabled = false
        menu.addItem(state)
        let open = NSMenuItem(title: "打开 KnowledgeDebt", action: #selector(openApplication), keyEquivalent: "o")
        open.target = self
        open.isEnabled = false
        menu.addItem(open)
        let showLogs = NSMenuItem(title: "显示诊断日志", action: #selector(showDiagnosticLogs), keyEquivalent: "l")
        showLogs.target = self
        menu.addItem(showLogs)
        menu.addItem(.separator())
        let quit = NSMenuItem(title: "退出 KnowledgeDebt", action: #selector(quitApplication), keyEquivalent: "q")
        quit.target = self
        menu.addItem(quit)
        item.menu = menu
        statusItem = item
        stateItem = state
        openItem = open
    }

    private func startServices() throws {
        guard let resources = Bundle.main.resourceURL else {
            throw LauncherError.missingRuntime("Contents/Resources")
        }
        let backendExecutable = resources.appendingPathComponent("backend/knowledgedebt-api")
        let nodeExecutable = resources.appendingPathComponent("runtime/node/bin/node")
        let webRoot = resources.appendingPathComponent("web", isDirectory: true)
        let webServer = webRoot.appendingPathComponent("server.js")
        let ffmpeg = resources.appendingPathComponent("runtime/ffmpeg/bin/ffmpeg")
        let ffprobe = resources.appendingPathComponent("runtime/ffmpeg/bin/ffprobe")
        let whisper = resources.appendingPathComponent("runtime/whisper/bin/whisper-cli")
        for required in [backendExecutable, nodeExecutable, webServer, ffmpeg, ffprobe, whisper] where !FileManager.default.fileExists(atPath: required.path) {
            throw LauncherError.missingRuntime(required.path)
        }

        guard let apiPort = freeLoopbackPort(), let webPort = freeLoopbackPort(), apiPort != webPort else {
            throw LauncherError.noFreePort
        }
        let accessToken = UUID().uuidString.replacingOccurrences(of: "-", with: "")
        let encryptionKey = try persistentEncryptionKey()
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0.2.0"
        let commonEnvironment = [
            "HOME": FileManager.default.homeDirectoryForCurrentUser.path,
            "LANG": "zh_CN.UTF-8",
            "LC_ALL": "zh_CN.UTF-8",
            "PATH": "\(resources.appendingPathComponent("runtime/ffmpeg/bin").path):\(resources.appendingPathComponent("runtime/whisper/bin").path):/usr/bin:/bin:/usr/sbin:/sbin",
        ]

        let backendLog = try logHandle(name: "backend.log")
        let backendProcess = Process()
        backendProcess.executableURL = backendExecutable
        backendProcess.currentDirectoryURL = resources
        backendProcess.environment = commonEnvironment.merging([
            "KNOWLEDGEDEBT_ACCESS_TOKEN": accessToken,
            "KNOWLEDGEDEBT_API_PORT": String(apiPort),
            "KNOWLEDGEDEBT_APP_VERSION": version,
            "KNOWLEDGEDEBT_DATA_DIR": dataRoot.path,
            "KNOWLEDGEDEBT_ENCRYPTION_KEY": encryptionKey,
            "KNOWLEDGEDEBT_FFMPEG_PATH": ffmpeg.path,
            "KNOWLEDGEDEBT_LOCAL_ASR_BINARY": whisper.path,
            "KNOWLEDGEDEBT_LOCAL_ASR_MODEL_DIR": dataRoot.appendingPathComponent("models", isDirectory: true).path,
        ]) { _, latest in latest }
        backendProcess.standardOutput = backendLog
        backendProcess.standardError = backendLog
        watch(process: backendProcess, name: "本地 API")
        try backendProcess.run()
        backend = backendProcess

        let webLog = try logHandle(name: "web.log")
        let webProcess = Process()
        webProcess.executableURL = nodeExecutable
        webProcess.arguments = [webServer.path]
        webProcess.currentDirectoryURL = webRoot
        webProcess.environment = commonEnvironment.merging([
            "HOSTNAME": "127.0.0.1",
            "KNOWLEDGEDEBT_ACCESS_TOKEN": accessToken,
            "KNOWLEDGEDEBT_API_URL": "http://127.0.0.1:\(apiPort)",
            "NODE_ENV": "production",
            "PORT": String(webPort),
        ]) { _, latest in latest }
        webProcess.standardOutput = webLog
        webProcess.standardError = webLog
        watch(process: webProcess, name: "课程工作台")
        try webProcess.run()
        web = webProcess

        webURL = URL(string: "http://127.0.0.1:\(webPort)")
        try webURL?.absoluteString.write(
            to: runtimeState.appendingPathComponent("web-url.txt"),
            atomically: true,
            encoding: .utf8
        )
        waitForServices(apiPort: apiPort, webPort: webPort)
    }

    private func waitForServices(apiPort: Int, webPort: Int) {
        Thread.detachNewThread { [weak self] in
            guard let self else { return }
            let deadline = Date().addingTimeInterval(90)
            var apiReady = false
            var webReady = false
            while Date() < deadline && !self.stopping {
                apiReady = apiReady || self.isHealthy(URL(string: "http://127.0.0.1:\(apiPort)/health")!)
                webReady = webReady || self.isHealthy(URL(string: "http://127.0.0.1:\(webPort)")!)
                if apiReady && webReady {
                    DispatchQueue.main.async {
                        self.stateItem?.title = "本地服务运行中"
                        self.openItem?.isEnabled = true
                        self.openApplication()
                    }
                    return
                }
                Thread.sleep(forTimeInterval: 0.35)
            }
            guard !self.stopping else { return }
            DispatchQueue.main.async {
                self.presentFatal(NSError(
                    domain: "KnowledgeDebtLauncher",
                    code: 1,
                    userInfo: [NSLocalizedDescriptionKey: "本地服务在 90 秒内未通过健康检查。原始数据未删除，请查看诊断日志。"]
                ))
            }
        }
    }

    private func isHealthy(_ url: URL) -> Bool {
        let semaphore = DispatchSemaphore(value: 0)
        var healthy = false
        var request = URLRequest(url: url)
        request.timeoutInterval = 2
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 2
        let session = URLSession(configuration: configuration)
        let task = session.dataTask(with: request) { _, response, _ in
            if let response = response as? HTTPURLResponse {
                healthy = (200..<400).contains(response.statusCode)
            }
            semaphore.signal()
        }
        task.resume()
        _ = semaphore.wait(timeout: .now() + 3)
        task.cancel()
        session.invalidateAndCancel()
        return healthy
    }

    private func freeLoopbackPort() -> Int? {
        let descriptor = socket(AF_INET, SOCK_STREAM, 0)
        guard descriptor >= 0 else { return nil }
        defer { close(descriptor) }
        var address = sockaddr_in()
        address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        address.sin_family = sa_family_t(AF_INET)
        address.sin_port = in_port_t(0)
        address.sin_addr = in_addr(s_addr: inet_addr("127.0.0.1"))
        let result = withUnsafePointer(to: &address) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.bind(descriptor, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        guard result == 0 else { return nil }
        var length = socklen_t(MemoryLayout<sockaddr_in>.size)
        let nameResult = withUnsafeMutablePointer(to: &address) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                getsockname(descriptor, $0, &length)
            }
        }
        guard nameResult == 0 else { return nil }
        return Int(UInt16(bigEndian: address.sin_port))
    }

    private func persistentEncryptionKey() throws -> String {
        let service = "io.github.hanzhiovo.KnowledgeDebt"
        let account = "provider-encryption-key"
        let lookup: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecMatchLimit as String: kSecMatchLimitOne,
            kSecReturnData as String: true,
        ]
        var item: CFTypeRef?
        let status = SecItemCopyMatching(lookup as CFDictionary, &item)
        if status == errSecSuccess, let data = item as? Data, let value = String(data: data, encoding: .utf8) {
            return value
        }
        guard status == errSecItemNotFound else { throw LauncherError.keychain(status) }
        var bytes = [UInt8](repeating: 0, count: 32)
        let randomStatus = SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes)
        guard randomStatus == errSecSuccess else { throw LauncherError.keychain(randomStatus) }
        let value = Data(bytes).base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
        let add: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
            kSecValueData as String: Data(value.utf8),
        ]
        let addStatus = SecItemAdd(add as CFDictionary, nil)
        guard addStatus == errSecSuccess else { throw LauncherError.keychain(addStatus) }
        return value
    }

    private func logHandle(name: String) throws -> FileHandle {
        let url = logs.appendingPathComponent(name)
        if !FileManager.default.fileExists(atPath: url.path) {
            FileManager.default.createFile(atPath: url.path, contents: nil)
        }
        let handle = try FileHandle(forWritingTo: url)
        try handle.seekToEnd()
        let marker = "\n=== KnowledgeDebt launch \(ISO8601DateFormatter().string(from: Date())) ===\n"
        try handle.write(contentsOf: Data(marker.utf8))
        logHandles.append(handle)
        return handle
    }

    private func watch(process: Process, name: String) {
        process.terminationHandler = { [weak self] process in
            guard let self, !self.stopping else { return }
            DispatchQueue.main.async {
                self.presentFatal(LauncherError.childExited(name, process.terminationStatus))
            }
        }
    }

    private func stop(process: Process?) {
        guard let process, process.isRunning else { return }
        process.terminate()
        let deadline = Date().addingTimeInterval(5)
        while process.isRunning && Date() < deadline {
            Thread.sleep(forTimeInterval: 0.05)
        }
        if process.isRunning {
            kill(process.processIdentifier, SIGKILL)
        }
    }

    private func openExistingInstance() {
        let stateURL = runtimeState.appendingPathComponent("web-url.txt")
        if let value = try? String(contentsOf: stateURL, encoding: .utf8), let url = URL(string: value.trimmingCharacters(in: .whitespacesAndNewlines)) {
            NSWorkspace.shared.open(url)
        }
    }

    @objc private func openApplication() {
        if let webURL {
            NSWorkspace.shared.open(webURL)
        }
    }

    @objc private func showDiagnosticLogs() {
        NSWorkspace.shared.open(logs)
    }

    @objc private func quitApplication() {
        NSApp.terminate(nil)
    }

    private func presentFatal(_ error: Error) {
        guard !stopping else { return }
        stopping = true
        stateItem?.title = "启动失败"
        let alert = NSAlert()
        alert.alertStyle = .critical
        alert.messageText = "KnowledgeDebt 无法启动"
        alert.informativeText = error.localizedDescription
        alert.addButton(withTitle: "显示日志")
        alert.addButton(withTitle: "退出")
        NSApp.activate(ignoringOtherApps: true)
        if alert.runModal() == .alertFirstButtonReturn {
            showDiagnosticLogs()
        }
        NSApp.terminate(nil)
    }
}

let application = NSApplication.shared
let delegate = AppDelegate()
application.delegate = delegate
application.run()
