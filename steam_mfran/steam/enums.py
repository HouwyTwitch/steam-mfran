"""Steam enums (subset) used by the app."""

from __future__ import annotations

from enum import IntEnum


class EMsg(IntEnum):
    Multi = 1
    ServiceMethod = 146
    ServiceMethodResponse = 147
    ServiceMethodCallFromClient = 151
    ClientHeartBeat = 703
    ClientLogOff = 706
    ClientChangeStatus = 716
    ClientLogOnResponse = 751
    ClientLoggedOff = 757
    ClientPersonaState = 766
    ClientFriendsList = 767
    ClientRequestFriendData = 815
    ClientServerUnavailable = 5500
    ClientLogon = 5514


class EResult(IntEnum):
    Invalid = 0
    OK = 1
    Fail = 2
    NoConnection = 3
    InvalidPassword = 5
    LoggedInElsewhere = 6
    InvalidProtocolVer = 7
    InvalidParam = 8
    FileNotFound = 9
    Busy = 10
    InvalidState = 11
    InvalidName = 12
    InvalidEmail = 13
    DuplicateName = 14
    AccessDenied = 15
    Timeout = 16
    Banned = 17
    AccountNotFound = 18
    InvalidSteamID = 19
    ServiceUnavailable = 20
    NotLoggedOn = 21
    Pending = 22
    EncryptionFailure = 23
    InsufficientPrivilege = 24
    LimitExceeded = 25
    Revoked = 26
    Expired = 27
    AlreadyRedeemed = 28
    DuplicateRequest = 29
    AlreadyOwned = 30
    IPNotFound = 31
    PersistFailed = 32
    LockingFailed = 33
    LogonSessionReplaced = 34
    ConnectFailed = 35
    HandshakeFailed = 36
    IOFailure = 37
    RemoteDisconnect = 38
    Blocked = 40
    Ignored = 41
    NoMatch = 42
    AccountDisabled = 43
    ServiceReadOnly = 44
    AccountNotFeatured = 45
    AdministratorOK = 46
    ContentVersion = 47
    TryAnotherCM = 48
    PasswordRequiredToKickSession = 49
    AlreadyLoggedInElsewhere = 50
    Suspended = 51
    Cancelled = 52
    DataCorruption = 53
    DiskFull = 54
    RemoteCallFailed = 55
    ExternalAccountUnlinked = 57
    PSNTicketInvalid = 58
    ExternalAccountAlreadyLinked = 59
    RemoteFileConflict = 60
    IllegalPassword = 61
    SameAsPreviousValue = 62
    AccountLogonDenied = 63
    CannotUseOldPassword = 64
    InvalidLoginAuthCode = 65
    AccountLogonDeniedNoMail = 66
    HardwareNotCapableOfIPT = 67
    IPTInitError = 68
    ParentalControlRestricted = 69
    FacebookQueryError = 70
    ExpiredLoginAuthCode = 71
    IPLoginRestrictionFailed = 72
    AccountLockedDown = 73
    AccountLogonDeniedVerifiedEmailRequired = 74
    NoMatchingURL = 75
    BadResponse = 76
    RequirePasswordReEntry = 77
    ValueOutOfRange = 78
    UnexpectedError = 79
    Disabled = 80
    InvalidCEGSubmission = 81
    RestrictedDevice = 82
    RegionLocked = 83
    RateLimitExceeded = 84
    AccountLoginDeniedNeedTwoFactor = 85
    ItemDeleted = 86
    AccountLoginDeniedThrottle = 87
    TwoFactorCodeMismatch = 88
    TwoFactorActivationCodeMismatch = 89
    AccountAssociatedToMultiplePartners = 90
    NotModified = 91
    NoMobileDevice = 92
    TimeNotSynced = 93
    SMSCodeFailed = 94
    AccountLimitExceeded = 95
    AccountActivityLimitExceeded = 96
    PhoneActivityLimitExceeded = 97
    RefundToWallet = 98
    EmailSendFailure = 99
    NotSettled = 100
    NeedCaptcha = 101
    GSLTDenied = 102
    GSOwnerDenied = 103
    InvalidItemType = 104
    IPBanned = 105
    GSLTExpired = 106
    InsufficientFunds = 107
    TooManyPending = 108
    NoSiteLicensesFound = 109
    WGNetworkSendExceeded = 110
    AccountNotFriends = 111
    LimitedUserAccount = 112
    CantRemoveItem = 113
    AccountDeleted = 114
    ExistingUserCancelledLicense = 115
    CommunityCooldown = 116
    NoLauncherSpecified = 117
    MustAgreeToSSA = 118
    LauncherMigrated = 119
    SteamRealmMismatch = 120
    InvalidSignature = 121
    ParseFailure = 122
    NoVerifiedPhone = 123
    InsufficientBattery = 124
    ChargerRequired = 125
    CachedCredentialInvalid = 126
    PhoneNumberIsVOIP = 127
    NotSupported = 128
    FamilySizeLimitExceeded = 129
    OfflineAppCacheInvalid = 130

    @classmethod
    def name_of(cls, value: int) -> str:
        try:
            return cls(value).name
        except ValueError:
            return f"EResult({value})"


class EAuthSessionGuardType(IntEnum):
    Unknown = 0
    NoGuard = 1
    EmailCode = 2
    DeviceCode = 3
    DeviceConfirmation = 4
    EmailConfirmation = 5
    MachineToken = 6
    LegacyMachineAuth = 7


class EAuthTokenPlatformType(IntEnum):
    Unknown = 0
    SteamClient = 1
    WebBrowser = 2
    MobileApp = 3


class ESessionPersistence(IntEnum):
    Ephemeral = 0
    Persistent = 1


class EFriendRelationship(IntEnum):
    NoRelationship = 0
    Blocked = 1
    RequestRecipient = 2  # someone sent *us* an invite
    Friend = 3
    RequestInitiator = 4  # we sent an invite
    Ignored = 5
    IgnoredFriend = 6


class EPersonaState(IntEnum):
    Offline = 0
    Online = 1
    Busy = 2
    Away = 3
    Snooze = 4
    LookingToTrade = 5
    LookingToPlay = 6
    Invisible = 7


class EChatEntryType(IntEnum):
    Invalid = 0
    ChatMsg = 1
    Typing = 2
    InviteGame = 3
    LeftConversation = 6
    Entered = 7
    WasKicked = 8
    WasBanned = 9
    Disconnected = 10
    HistoricalChat = 11
    LinkBlocked = 14


# EClientPersonaStateFlag
PERSONA_FLAGS_DEFAULT = 1 | 2 | 16 | 64 | 256  # Status | PlayerName | Presence | LastSeen | GameExtraInfo

# k_EOSTypeWeb (-700) as uint32, used by the web chat client
OS_TYPE_WEB = 4294966596
PROTOCOL_VERSION = 65580
