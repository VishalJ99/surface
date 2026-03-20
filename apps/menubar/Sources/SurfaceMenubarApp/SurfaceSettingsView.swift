import SwiftUI
import SurfaceMenubarCore

struct SurfaceSettingsView: View {
    @ObservedObject var model: SurfaceMenubarModel
    @AppStorage("surfaceHomePath") private var surfaceHomePath = SurfacePaths.defaultSurfaceHomePath()
    @AppStorage("repositoryRootPath") private var repositoryRootPath = SurfacePaths.defaultRepositoryRootPath()

    var body: some View {
        Form {
            Section("Accounts") {
                Text("Account connection still lives in the Surface CLI for this phase.")
                Text("Use `python surface account setup --provider ... --account ...` to connect Gmail or Outlook accounts.")
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
            }

            Section("Sync") {
                TextField("Surface home", text: $surfaceHomePath)
                    .textFieldStyle(.roundedBorder)
                TextField("Repo root", text: $repositoryRootPath)
                    .textFieldStyle(.roundedBorder)

                HStack {
                    Button("Reload Snapshot") {
                        model.load(surfaceHomePath: surfaceHomePath)
                    }

                    Button("Sync Now") {
                        Task {
                            await model.syncNow(surfaceHomePath: surfaceHomePath, repositoryRootPath: repositoryRootPath)
                        }
                    }
                    .disabled(model.isSyncing)
                }

                Text("`Sync Now` runs `conda run -n surface-app python surface sync run` from the configured repo root.")
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
            }

            Section("Status") {
                if let snapshot = model.snapshot {
                    Text("Loaded \(snapshot.itemCount) unread items across \(snapshot.mailboxCount) mailboxes.")
                } else if let errorMessage = model.errorMessage {
                    Text(errorMessage)
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                } else {
                    Text("No snapshot loaded yet.")
                        .foregroundStyle(.secondary)
                }

                if let output = model.lastCommandOutput, !output.isEmpty {
                    Text(output)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                        .lineLimit(4)
                }
            }
        }
        .formStyle(.grouped)
        .padding(20)
    }
}
