import Foundation

public struct SurfacePaths: Equatable, Sendable {
    public let surfaceHome: URL

    public init(surfaceHome: URL) {
        self.surfaceHome = surfaceHome
    }

    public init(surfaceHomePath: String) {
        self.surfaceHome = URL(fileURLWithPath: NSString(string: surfaceHomePath).expandingTildeInPath, isDirectory: true)
    }

    public var menubarViewPath: URL {
        surfaceHome.appending(path: "exports/filtered/menubar-inbox.json")
    }

    public var syncStatusPath: URL {
        surfaceHome.appending(path: "ui/sync-status.json")
    }

    public static func defaultSurfaceHomePath(
        environment: [String: String] = ProcessInfo.processInfo.environment,
        homeDirectory: URL = FileManager.default.homeDirectoryForCurrentUser
    ) -> String {
        if let explicit = environment["SURFACE_HOME"], !explicit.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return explicit
        }
        return homeDirectory.appending(path: ".surface").path
    }

    public static func defaultRepositoryRootPath(
        environment: [String: String] = ProcessInfo.processInfo.environment,
        fileManager: FileManager = .default
    ) -> String {
        if let explicit = environment["SURFACE_REPO_ROOT"], !explicit.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return explicit
        }

        let currentDirectory = fileManager.currentDirectoryPath
        let candidate = URL(fileURLWithPath: currentDirectory, isDirectory: true)
        let surfaceEntrypoint = candidate.appending(path: "surface").path
        let gitDirectory = candidate.appending(path: ".git").path
        if fileManager.fileExists(atPath: surfaceEntrypoint), fileManager.fileExists(atPath: gitDirectory) {
            return currentDirectory
        }

        return ""
    }
}

public enum MenubarStoreError: LocalizedError, Equatable {
    case missingArtifact(String)
    case invalidContract(String)
    case decodeFailure(String)

    public var errorDescription: String? {
        switch self {
        case .missingArtifact(let path):
            return "Menubar snapshot not found at \(path). Run `python surface sync run` first."
        case .invalidContract(let contract):
            return "Unsupported menubar contract: \(contract)"
        case .decodeFailure(let message):
            return "Failed to decode menubar snapshot: \(message)"
        }
    }
}

public struct MenubarArtifactStore {
    public init() {}

    public func loadView(at paths: SurfacePaths) throws -> SurfaceMenubarView {
        let url = paths.menubarViewPath
        guard FileManager.default.fileExists(atPath: url.path) else {
            throw MenubarStoreError.missingArtifact(url.path)
        }

        let data = try Data(contentsOf: url)
        let decoder = JSONDecoder()
        do {
            let view = try decoder.decode(SurfaceMenubarView.self, from: data)
            guard view.contract == "surface.filtered_menubar.v1" else {
                throw MenubarStoreError.invalidContract(view.contract)
            }
            return view
        } catch let error as MenubarStoreError {
            throw error
        } catch {
            throw MenubarStoreError.decodeFailure(error.localizedDescription)
        }
    }
}

public enum SurfaceDateFormatting {
    public static func shortTimestamp(_ value: String?) -> String? {
        guard let value, let date = parseISO8601(value) else {
            return nil
        }

        let formatter = DateFormatter()
        formatter.dateStyle = .medium
        formatter.timeStyle = .short
        return formatter.string(from: date)
    }

    public static func parseISO8601(_ value: String) -> Date? {
        for formatter in makeISO8601Formatters() {
            if let date = formatter.date(from: value) {
                return date
            }
        }
        return nil
    }

    private static func makeISO8601Formatters() -> [ISO8601DateFormatter] {
        let internet = ISO8601DateFormatter()
        internet.formatOptions = [.withInternetDateTime]

        let fractional = ISO8601DateFormatter()
        fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return [fractional, internet]
    }
}
