import Foundation

struct CaptureCoverageEvidence: Encodable {
    let basis = "callbackUptime"
    let captureStopUptimeNanoseconds: UInt64?
    let sourceEndSeparationSeconds: Double?
    let systemTrailingGapToPeerSeconds: Double?
    let microphoneTrailingGapToPeerSeconds: Double?
    let systemTrailingGapToCaptureStopSeconds: Double?
    let microphoneTrailingGapToCaptureStopSeconds: Double?
    let sourceEndFreshnessToleranceSeconds: Double
    let bothSourcesReachedCommonEnd: Bool
    let bothSourcesFreshAtCaptureStop: Bool

    private enum CodingKeys: String, CodingKey {
        case basis, captureStopUptimeNanoseconds, sourceEndSeparationSeconds
        case systemTrailingGapToPeerSeconds, microphoneTrailingGapToPeerSeconds
        case systemTrailingGapToCaptureStopSeconds, microphoneTrailingGapToCaptureStopSeconds
        case sourceEndFreshnessToleranceSeconds, bothSourcesReachedCommonEnd
        case bothSourcesFreshAtCaptureStop
    }

    func encode(to encoder: Encoder) throws {
        var values = encoder.container(keyedBy: CodingKeys.self)
        try values.encode(basis, forKey: .basis)
        try values.encodeOptional(captureStopUptimeNanoseconds,
                                  forKey: .captureStopUptimeNanoseconds)
        try values.encodeOptional(sourceEndSeparationSeconds,
                                  forKey: .sourceEndSeparationSeconds)
        try values.encodeOptional(systemTrailingGapToPeerSeconds,
                                  forKey: .systemTrailingGapToPeerSeconds)
        try values.encodeOptional(microphoneTrailingGapToPeerSeconds,
                                  forKey: .microphoneTrailingGapToPeerSeconds)
        try values.encodeOptional(systemTrailingGapToCaptureStopSeconds,
                                  forKey: .systemTrailingGapToCaptureStopSeconds)
        try values.encodeOptional(microphoneTrailingGapToCaptureStopSeconds,
                                  forKey: .microphoneTrailingGapToCaptureStopSeconds)
        try values.encode(sourceEndFreshnessToleranceSeconds,
                          forKey: .sourceEndFreshnessToleranceSeconds)
        try values.encode(bothSourcesReachedCommonEnd, forKey: .bothSourcesReachedCommonEnd)
        try values.encode(bothSourcesFreshAtCaptureStop, forKey: .bothSourcesFreshAtCaptureStop)
    }
}

enum CaptureCoverage {
    static let sourceEndFreshnessToleranceSeconds = 1.0

    static func evidence(
        systemLastCallbackUptimeNanoseconds: UInt64?,
        microphoneLastCallbackUptimeNanoseconds: UInt64?,
        captureStopUptimeNanoseconds: UInt64?
    ) -> CaptureCoverageEvidence {
        let commonEndUptimeNanoseconds: UInt64?
        if let systemLastCallbackUptimeNanoseconds,
           let microphoneLastCallbackUptimeNanoseconds {
            commonEndUptimeNanoseconds = max(
                systemLastCallbackUptimeNanoseconds,
                microphoneLastCallbackUptimeNanoseconds)
        } else {
            commonEndUptimeNanoseconds = nil
        }
        let systemPeerGap = trailingGapSeconds(
            end: systemLastCallbackUptimeNanoseconds,
            boundary: commonEndUptimeNanoseconds)
        let microphonePeerGap = trailingGapSeconds(
            end: microphoneLastCallbackUptimeNanoseconds,
            boundary: commonEndUptimeNanoseconds)
        let separation = absoluteDifferenceSeconds(
            systemLastCallbackUptimeNanoseconds,
            microphoneLastCallbackUptimeNanoseconds)
        let systemStopGap = trailingGapSeconds(
            end: systemLastCallbackUptimeNanoseconds,
            boundary: captureStopUptimeNanoseconds)
        let microphoneStopGap = trailingGapSeconds(
            end: microphoneLastCallbackUptimeNanoseconds,
            boundary: captureStopUptimeNanoseconds)
        let tolerance = sourceEndFreshnessToleranceSeconds

        return CaptureCoverageEvidence(
            captureStopUptimeNanoseconds: captureStopUptimeNanoseconds,
            sourceEndSeparationSeconds: separation,
            systemTrailingGapToPeerSeconds: systemPeerGap,
            microphoneTrailingGapToPeerSeconds: microphonePeerGap,
            systemTrailingGapToCaptureStopSeconds: systemStopGap,
            microphoneTrailingGapToCaptureStopSeconds: microphoneStopGap,
            sourceEndFreshnessToleranceSeconds: tolerance,
            bothSourcesReachedCommonEnd: separation.map { $0 <= tolerance } == true,
            bothSourcesFreshAtCaptureStop: systemStopGap.map { $0 <= tolerance } == true &&
                microphoneStopGap.map { $0 <= tolerance } == true)
    }

    private static func absoluteDifferenceSeconds(_ lhs: UInt64?, _ rhs: UInt64?) -> Double? {
        guard let lhs, let rhs else { return nil }
        let difference = lhs >= rhs ? lhs - rhs : rhs - lhs
        let seconds = Double(difference) / 1_000_000_000
        return seconds.isFinite ? seconds : nil
    }

    private static func trailingGapSeconds(end: UInt64?, boundary: UInt64?) -> Double? {
        guard let end, let boundary, boundary >= end else { return nil }
        let seconds = Double(boundary - end) / 1_000_000_000
        return seconds.isFinite ? seconds : nil
    }
}

private extension KeyedEncodingContainer {
    mutating func encodeOptional<T: Encodable>(_ value: T?, forKey key: Key) throws {
        if let value { try encode(value, forKey: key) }
        else { try encodeNil(forKey: key) }
    }
}
