import Foundation
import Testing
@testable import SurfaceMenubarCore

@Test
func defaultSurfaceHomeFallsBackToHomeDirectory() {
    let home = URL(fileURLWithPath: "/Users/tester", isDirectory: true)
    #expect(
        SurfacePaths.defaultSurfaceHomePath(environment: [:], homeDirectory: home) == "/Users/tester/.surface"
    )
}

@Test
func defaultSurfaceHomeHonorsEnvironmentOverride() {
    #expect(
        SurfacePaths.defaultSurfaceHomePath(environment: ["SURFACE_HOME": "/tmp/surface-home"]) == "/tmp/surface-home"
    )
}

@Test
func menubarViewDecodesExpectedContract() throws {
    let json = """
    {
      "contract": "surface.filtered_menubar.v1",
      "generated_at": "2026-03-20T15:04:00+00:00",
      "selection_mode": "unread",
      "item_count": 1,
      "mailbox_count": 1,
      "sync_status": {
        "state": "idle",
        "last_attempt_at": "2026-03-20T15:03:57+00:00",
        "last_success_at": "2026-03-20T15:04:00+00:00",
        "next_scheduled_at": null,
        "error": null,
        "account_error_count": 0
      },
      "mailboxes": [
        {
          "provider": "gmail",
          "account": "personal",
          "label": "personal",
          "email_address": "person@example.com",
          "unread_count": 1,
          "items": [
            {
              "provider": "gmail",
              "account": "personal",
              "message_id": "abc123",
              "conversation_id": "thread1",
              "conversation_thread_id": "thread1",
              "internet_message_id": "<abc@example.com>",
              "sender_primary": "Buzzanca, Giorgio",
              "sender_email": "g@example.com",
              "subject": "Meeting Vishal",
              "preview": "Microsoft Teams meeting invitation",
              "received_at": "2026-02-23T13:13:38+00:00",
              "relative_time": "3w",
              "thread_message_count": 1,
              "can_rsvp": true,
              "available_actions": ["AcceptItem", "DeclineItem"],
              "meeting": {
                "request_type": "REQUEST",
                "response_type": "NEEDS-ACTION",
                "organizer": {
                  "name": "Buzzanca, Giorgio",
                  "email": "g@example.com"
                },
                "location": "Microsoft Teams",
                "start": "2026-02-23T13:00:00+00:00",
                "end": "2026-02-23T14:00:00+00:00",
                "available_rsvp_actions": ["AcceptItem", "DeclineItem"]
              }
            }
          ]
        }
      ]
    }
    """

    let temporaryDirectory = FileManager.default.temporaryDirectory
        .appending(path: UUID().uuidString, directoryHint: .isDirectory)
    try FileManager.default.createDirectory(at: temporaryDirectory, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: temporaryDirectory) }

    let surfaceHome = temporaryDirectory.appending(path: ".surface", directoryHint: .isDirectory)
    let viewPath = surfaceHome.appending(path: "exports/filtered/menubar-inbox.json")
    try FileManager.default.createDirectory(at: viewPath.deletingLastPathComponent(), withIntermediateDirectories: true)
    try Data(json.utf8).write(to: viewPath)

    let store = MenubarArtifactStore()
    let view = try store.loadView(at: SurfacePaths(surfaceHome: surfaceHome))

    #expect(view.contract == "surface.filtered_menubar.v1")
    #expect(view.itemCount == 1)
    #expect(view.mailboxes.count == 1)
    #expect(view.mailboxes[0].items[0].canRSVP)
    #expect(view.mailboxes[0].items[0].meeting?.location == "Microsoft Teams")
}
