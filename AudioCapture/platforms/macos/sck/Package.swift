// swift-tools-version: 6.0

import PackageDescription

let package = Package(
    name: "SCKAudioSpike",
    platforms: [.macOS(.v15)],
    targets: [
        .executableTarget(name: "sck-audio-spike")
    ],
    swiftLanguageModes: [.v5]
)
