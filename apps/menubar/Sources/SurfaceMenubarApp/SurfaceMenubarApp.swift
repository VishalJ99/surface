import AppKit
import SwiftUI
import SurfaceMenubarCore

@main
struct SurfaceMenubarApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var model = SurfaceMenubarModel()
    @AppStorage("surfaceHomePath") private var surfaceHomePath = SurfacePaths.defaultSurfaceHomePath()
    @AppStorage("repositoryRootPath") private var repositoryRootPath = SurfacePaths.defaultRepositoryRootPath()

    var body: some Scene {
        MenuBarExtra {
            MenubarContentView(model: model)
                .frame(width: 420, height: 560)
                .onAppear {
                    model.load(surfaceHomePath: surfaceHomePath)
                }
        } label: {
            Label {
                if model.unreadCount > 0 {
                    Text("\(model.unreadCount)")
                }
            } icon: {
                Image(systemName: model.unreadCount > 0 ? "tray.full" : "tray")
            }
        }
        .menuBarExtraStyle(.window)

        Settings {
            SurfaceSettingsView(model: model)
                .frame(width: 520, height: 320)
        }

        .commands {
            CommandMenu("Surface") {
                Button("Reload Snapshot") {
                    model.load(surfaceHomePath: surfaceHomePath)
                }
                .keyboardShortcut("r", modifiers: [.command])

                Button("Sync Now") {
                    Task {
                        await model.syncNow(surfaceHomePath: surfaceHomePath, repositoryRootPath: repositoryRootPath)
                    }
                }
                .keyboardShortcut("r", modifiers: [.command, .shift])
                .disabled(model.isSyncing)
            }
        }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
    }
}
