import Foundation
import SurfaceMenubarCore

@MainActor
final class SurfaceMenubarModel: ObservableObject {
    @Published private(set) var snapshot: SurfaceMenubarView?
    @Published private(set) var isLoading = false
    @Published private(set) var isSyncing = false
    @Published var errorMessage: String?
    @Published var expandedItemIDs: Set<String> = []
    @Published var lastCommandOutput: String?

    private let store = MenubarArtifactStore()

    var unreadCount: Int {
        snapshot?.itemCount ?? 0
    }

    func load(surfaceHomePath: String) {
        isLoading = true
        defer { isLoading = false }

        do {
            let view = try store.loadView(at: SurfacePaths(surfaceHomePath: surfaceHomePath))
            snapshot = view
            errorMessage = nil
        } catch {
            snapshot = nil
            errorMessage = error.localizedDescription
        }
    }

    func toggleExpansion(for itemID: String) {
        if expandedItemIDs.contains(itemID) {
            expandedItemIDs.remove(itemID)
        } else {
            expandedItemIDs.insert(itemID)
        }
    }

    func isExpanded(_ itemID: String) -> Bool {
        expandedItemIDs.contains(itemID)
    }

    func syncNow(surfaceHomePath: String, repositoryRootPath: String) async {
        let trimmedRoot = repositoryRootPath.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmedRoot.isEmpty else {
            errorMessage = "Set the repo root in Settings before running sync from the app shell."
            return
        }

        isSyncing = true
        defer { isSyncing = false }

        do {
            let output = try await Task.detached(priority: .userInitiated) {
                try Self.runSyncCommand(surfaceHomePath: surfaceHomePath, repositoryRootPath: trimmedRoot)
            }.value
            lastCommandOutput = output
            load(surfaceHomePath: surfaceHomePath)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private nonisolated static func runSyncCommand(surfaceHomePath: String, repositoryRootPath: String) throws -> String {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        process.arguments = ["conda", "run", "-n", "surface-app", "python", "surface", "sync", "run"]
        process.currentDirectoryURL = URL(fileURLWithPath: NSString(string: repositoryRootPath).expandingTildeInPath, isDirectory: true)

        var environment = ProcessInfo.processInfo.environment
        environment["SURFACE_HOME"] = NSString(string: surfaceHomePath).expandingTildeInPath
        process.environment = environment

        let stdout = Pipe()
        let stderr = Pipe()
        process.standardOutput = stdout
        process.standardError = stderr

        try process.run()
        process.waitUntilExit()

        let outputData = stdout.fileHandleForReading.readDataToEndOfFile()
        let errorData = stderr.fileHandleForReading.readDataToEndOfFile()
        let output = String(decoding: outputData, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
        let errorOutput = String(decoding: errorData, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)

        guard process.terminationStatus == 0 else {
            let message = errorOutput.isEmpty ? output : errorOutput
            throw NSError(
                domain: "SurfaceMenubarApp",
                code: Int(process.terminationStatus),
                userInfo: [NSLocalizedDescriptionKey: message.isEmpty ? "surface sync run failed." : message]
            )
        }

        return output
    }
}
