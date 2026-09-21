import Foundation

struct CaptureCoverageEvidence: Encodable {
    let basis = "callbackUptime"
    let sourceEndSeparationSeconds: Double?
    let systemTrailingGapToPeerSeconds: Double?
    let microphoneTrailingGapToPeerSeconds: Double?
    let sourceEndFreshnessToleranceSeconds: Double
    let bothSourcesReachedCommonEnd: Bool

    private enum CodingKeys: String, CodingKey {
        case basis
        case sourceEndSeparationSeconds
        case systemTrailingGapToPeerSeconds
        case microphoneTrailingGapToPeerSeconds
        case sourceEndFreshnessToleranceSeconds
        case bothSourcesReachedCommonEnd
    }

    func encode(to encoder: Encoder) throws {
        var values = encoder.container(keyedBy: CodingKeys.self)
        try values.encode(basis, forKey: .basis)
        if let sourceEndSeparationSeconds {
            try values.encode(sourceEndSeparationSeconds, forKey: .sourceEndSeparationSeconds)
        } else {
            try values.encodeNil(forKey: .sourceEndSeparationSeconds)
        }
        if let systemTrailingGapToPeerSeconds {
            try values.encode(systemTrailingGapToPeerSeconds, forKey: .systemTrailingGapToPeerSeconds)
        } else {
            try values.encodeNil(forKey: .systemTrailingGapToPeerSeconds)
        }
        if let microphoneTrailingGapToPeerSeconds {
            try values.encode(microphoneTrailingGapToPeerSeconds, forKey: .microphoneTrailingGapToPeerSeconds)
        } else {
            try values.encodeNil(forKey: .microphoneTrailingGapToPeerSeconds)
        }
        try values.encode(sourceEndFreshnessToleranceSeconds,
                          forKey: .sourceEndFreshnessToleranceSeconds)
        try values.encode(bothSourcesReachedCommonEnd, forKey: .bothSourcesReachedCommonEnd)
    }
}

enum CaptureCoverage {
    static let sourceEndFreshnessToleranceSeconds = 1.0

    static func evidence(
        systemLastCallbackUptimeNanoseconds: UInt64?,
        microphoneLastCallbackUptimeNanoseconds: UInt64?
    ) -> CaptureCoverageEvidence {
        guard let systemLastCallbackUptimeNanoseconds,
              let microphoneLastCallbackUptimeNanoseconds,
              let endOffsetSeconds = signedSeconds(
                microphoneLastCallbackUptimeNanoseconds,
                systemLastCallbackUptimeNanoseconds)
        else {
            return CaptureCoverageEvidence(
                sourceEndSeparationSeconds: nil,
                systemTrailingGapToPeerSeconds: nil,
                microphoneTrailingGapToPeerSeconds: nil,
                sourceEndFreshnessToleranceSeconds: sourceEndFreshnessToleranceSeconds,
                bothSourcesReachedCommonEnd: false)
        }

        let sourceEndSeparationSeconds = abs(endOffsetSeconds)
        let systemTrailingGapToPeerSeconds = max(endOffsetSeconds, 0)
        let microphoneTrailingGapToPeerSeconds = max(-endOffsetSeconds, 0)
        return CaptureCoverageEvidence(
            sourceEndSeparationSeconds: sourceEndSeparationSeconds,
            systemTrailingGapToPeerSeconds: systemTrailingGapToPeerSeconds,
            microphoneTrailingGapToPeerSeconds: microphoneTrailingGapToPeerSeconds,
            sourceEndFreshnessToleranceSeconds: sourceEndFreshnessToleranceSeconds,
            bothSourcesReachedCommonEnd:
                sourceEndSeparationSeconds <= sourceEndFreshnessToleranceSeconds)
    }

    private static func signedSeconds(_ lhs: UInt64, _ rhs: UInt64) -> Double? {
        let nanoseconds = lhs >= rhs ? Double(lhs - rhs) : -Double(rhs - lhs)
        let seconds = nanoseconds / 1_000_000_000
        return seconds.isFinite ? seconds : nil
    }
}
