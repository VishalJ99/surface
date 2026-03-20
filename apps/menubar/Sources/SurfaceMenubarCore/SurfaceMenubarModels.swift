import Foundation

public struct SurfaceMenubarView: Codable, Equatable, Sendable {
    public let contract: String
    public let generatedAt: String
    public let selectionMode: String
    public let itemCount: Int
    public let mailboxCount: Int
    public let syncStatus: SyncStatus
    public let mailboxes: [MailboxGroup]

    enum CodingKeys: String, CodingKey {
        case contract
        case generatedAt = "generated_at"
        case selectionMode = "selection_mode"
        case itemCount = "item_count"
        case mailboxCount = "mailbox_count"
        case syncStatus = "sync_status"
        case mailboxes
    }
}

public struct SyncStatus: Codable, Equatable, Sendable {
    public let state: String
    public let lastAttemptAt: String?
    public let lastSuccessAt: String?
    public let nextScheduledAt: String?
    public let error: String?
    public let accountErrorCount: Int

    enum CodingKeys: String, CodingKey {
        case state
        case lastAttemptAt = "last_attempt_at"
        case lastSuccessAt = "last_success_at"
        case nextScheduledAt = "next_scheduled_at"
        case error
        case accountErrorCount = "account_error_count"
    }
}

public struct MailboxGroup: Codable, Equatable, Identifiable, Sendable {
    public let provider: String
    public let account: String
    public let label: String
    public let emailAddress: String?
    public let unreadCount: Int
    public let items: [MenubarItem]

    public var id: String {
        "\(provider)/\(account)"
    }

    enum CodingKeys: String, CodingKey {
        case provider
        case account
        case label
        case emailAddress = "email_address"
        case unreadCount = "unread_count"
        case items
    }
}

public struct MenubarItem: Codable, Equatable, Identifiable, Sendable {
    public let provider: String
    public let account: String
    public let messageID: String?
    public let conversationID: String?
    public let conversationThreadID: String?
    public let internetMessageID: String?
    public let senderPrimary: String
    public let senderEmail: String?
    public let subject: String
    public let preview: String
    public let receivedAt: String?
    public let relativeTime: String?
    public let threadMessageCount: Int
    public let canRSVP: Bool
    public let availableActions: [String]
    public let meeting: MeetingDetails?

    public var id: String {
        if let messageID {
            return "\(provider)/\(account)/\(messageID)"
        }
        if let conversationID {
            return "\(provider)/\(account)/\(conversationID)"
        }
        if let internetMessageID {
            return "\(provider)/\(account)/\(internetMessageID)"
        }
        return "\(provider)/\(account)/\(subject)"
    }

    enum CodingKeys: String, CodingKey {
        case provider
        case account
        case messageID = "message_id"
        case conversationID = "conversation_id"
        case conversationThreadID = "conversation_thread_id"
        case internetMessageID = "internet_message_id"
        case senderPrimary = "sender_primary"
        case senderEmail = "sender_email"
        case subject
        case preview
        case receivedAt = "received_at"
        case relativeTime = "relative_time"
        case threadMessageCount = "thread_message_count"
        case canRSVP = "can_rsvp"
        case availableActions = "available_actions"
        case meeting
    }
}

public struct MeetingDetails: Codable, Equatable, Sendable {
    public let requestType: String?
    public let responseType: String?
    public let organizer: Mailbox?
    public let location: String?
    public let start: String?
    public let end: String?
    public let availableRSVPActions: [String]

    enum CodingKeys: String, CodingKey {
        case requestType = "request_type"
        case responseType = "response_type"
        case organizer
        case location
        case start
        case end
        case availableRSVPActions = "available_rsvp_actions"
    }
}

public struct Mailbox: Codable, Equatable, Sendable {
    public let name: String
    public let email: String
}
