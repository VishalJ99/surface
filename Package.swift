// swift-tools-version: 6.1

import PackageDescription

let package = Package(
    name: "SurfaceMenubar",
    platforms: [
        .macOS(.v14),
    ],
    products: [
        .library(name: "SurfaceMenubarCore", targets: ["SurfaceMenubarCore"]),
        .executable(name: "SurfaceMenubarApp", targets: ["SurfaceMenubarApp"]),
    ],
    targets: [
        .target(
            name: "SurfaceMenubarCore",
            path: "apps/menubar/Sources/SurfaceMenubarCore"
        ),
        .executableTarget(
            name: "SurfaceMenubarApp",
            dependencies: ["SurfaceMenubarCore"],
            path: "apps/menubar/Sources/SurfaceMenubarApp"
        ),
        .testTarget(
            name: "SurfaceMenubarCoreTests",
            dependencies: ["SurfaceMenubarCore"],
            path: "apps/menubar/Tests/SurfaceMenubarCoreTests"
        ),
    ]
)
