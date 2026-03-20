import SwiftUI
import SurfaceMenubarCore

struct MenubarContentView: View {
    @ObservedObject var model: SurfaceMenubarModel
    @AppStorage("surfaceHomePath") private var surfaceHomePath = SurfacePaths.defaultSurfaceHomePath()
    @AppStorage("repositoryRootPath") private var repositoryRootPath = SurfacePaths.defaultRepositoryRootPath()
    @Environment(\.openSettings) private var openSettings

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            content
        }
        .background(.regularMaterial)
    }

    private var header: some View {
        HStack(alignment: .center, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Text("Surface")
                    .font(.headline)
                Text(statusLine)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Spacer()

            if model.isSyncing {
                ProgressView()
                    .controlSize(.small)
            }

            Button {
                model.load(surfaceHomePath: surfaceHomePath)
            } label: {
                Image(systemName: "arrow.clockwise")
            }
            .help("Reload snapshot")

            Button("Sync") {
                Task {
                    await model.syncNow(surfaceHomePath: surfaceHomePath, repositoryRootPath: repositoryRootPath)
                }
            }
            .disabled(model.isSyncing)

            Button {
                openSettings()
            } label: {
                Image(systemName: "gearshape")
            }
            .help("Open Settings")
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
    }

    @ViewBuilder
    private var content: some View {
        if let snapshot = model.snapshot {
            if snapshot.mailboxes.isEmpty {
                emptyState(
                    title: "No unread mail",
                    message: "The current menubar snapshot has no unread items."
                )
            } else {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 18) {
                        ForEach(snapshot.mailboxes) { mailbox in
                            MailboxSectionView(mailbox: mailbox, model: model)
                        }
                    }
                    .padding(16)
                }
            }
        } else if model.isLoading {
            VStack {
                Spacer()
                ProgressView("Loading inbox snapshot…")
                Spacer()
            }
        } else {
            emptyState(
                title: "No snapshot available",
                message: model.errorMessage ?? "Run `python surface sync run` to create the first menubar snapshot."
            )
        }
    }

    private var statusLine: String {
        guard let snapshot = model.snapshot else {
            return model.errorMessage ?? "Waiting for inbox snapshot"
        }

        let syncStatus = snapshot.syncStatus
        let statusPrefix = switch syncStatus.state {
        case "syncing":
            "Syncing"
        case "error":
            "Sync error"
        case "partial":
            "Partial sync"
        default:
            "Up to date"
        }

        if let timestamp = SurfaceDateFormatting.shortTimestamp(syncStatus.lastSuccessAt) {
            return "\(statusPrefix) • \(snapshot.itemCount) unread • \(timestamp)"
        }

        return "\(statusPrefix) • \(snapshot.itemCount) unread"
    }

    private func emptyState(title: String, message: String) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Spacer()
            Text(title)
                .font(.headline)
            Text(message)
                .font(.callout)
                .foregroundStyle(.secondary)
                .textSelection(.enabled)
            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .center)
        .padding(24)
    }
}

private struct MailboxSectionView: View {
    let mailbox: MailboxGroup
    @ObservedObject var model: SurfaceMenubarModel

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(mailbox.label)
                    .font(.headline)
                Text(mailbox.provider.uppercased())
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                Spacer()
                Text("\(mailbox.unreadCount)")
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
            }

            if let emailAddress = mailbox.emailAddress {
                Text(emailAddress)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            VStack(spacing: 8) {
                ForEach(mailbox.items) { item in
                    MessageRowView(item: item, isExpanded: model.isExpanded(item.id)) {
                        model.toggleExpansion(for: item.id)
                    }
                }
            }
        }
    }
}

private struct MessageRowView: View {
    let item: MenubarItem
    let isExpanded: Bool
    let onToggle: () -> Void

    var body: some View {
        Button(action: onToggle) {
            VStack(alignment: .leading, spacing: 8) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(item.senderPrimary)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(.primary)
                        .lineLimit(1)

                    Spacer()

                    if item.canRSVP {
                        Text("Invite")
                            .font(.caption2.weight(.semibold))
                            .padding(.horizontal, 6)
                            .padding(.vertical, 2)
                            .background(Capsule().fill(Color.accentColor.opacity(0.14)))
                            .foregroundStyle(Color.accentColor)
                    }

                    if let relativeTime = item.relativeTime {
                        Text(relativeTime)
                            .font(.caption.monospacedDigit())
                            .foregroundStyle(.secondary)
                    }
                }

                Text(item.subject.isEmpty ? "(No subject)" : item.subject)
                    .font(.callout)
                    .foregroundStyle(.primary)
                    .lineLimit(isExpanded ? 3 : 1)

                if isExpanded {
                    VStack(alignment: .leading, spacing: 6) {
                        if !item.preview.isEmpty {
                            Text(item.preview)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }

                        if let meeting = item.meeting {
                            MeetingSummaryView(meeting: meeting)
                        }
                    }
                    .transition(.opacity.combined(with: .move(edge: .top)))
                }
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .fill(Color(NSColor.controlBackgroundColor))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .strokeBorder(Color(NSColor.separatorColor).opacity(0.45), lineWidth: 1)
            )
        }
        .buttonStyle(.plain)
        .contentShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
    }
}

private struct MeetingSummaryView: View {
    let meeting: MeetingDetails

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            if let start = SurfaceDateFormatting.shortTimestamp(meeting.start) {
                Label(start, systemImage: "calendar")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            if let location = meeting.location, !location.isEmpty {
                Label(location, systemImage: "mappin.and.ellipse")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }
        }
    }
}
