cask "jamly" do
  arch arm: "arm64", intel: "x86_64"

  version "__VERSION__"
  sha256 arm:   "__SHA256_ARM64__",
         intel: "__SHA256_X86_64__"

  url "https://github.com/kurtisnotcurtis/jamly/releases/download/v#{version}/Jamly-#{version}-darwin-#{arch}.zip"
  name "Jamly"
  desc "Split media URLs or local audio into drums, drumless, or multi-stem tracks locally"
  homepage "https://kurtisnotcurtis.github.io/jamly/"

  livecheck do
    url :url
    strategy :github_latest
  end

  depends_on macos: :ventura

  app "Jamly.app"
  command_wrapper "jamly",
                  executable: "#{appdir}/Jamly.app/Contents/MacOS/Jamly"
  command_wrapper "jamly-runtime",
                  executable: "#{appdir}/Jamly.app/Contents/MacOS/jamly-runtime"

  zap trash: [
    "~/Library/Application Support/jamly",
    "~/Library/LaunchAgents/com.jamly.backend.plist",
    "~/Library/Saved Application State/com.jamly.companion.savedState",
  ]

  caveats <<~EOS
    Jamly.app is currently an unsigned, unnotarized preview build.
    On first launch, macOS Gatekeeper may block it; open Jamly from
    System Settings -> Privacy & Security (Open Anyway), or run:
      xattr -dr com.apple.quarantine "#{appdir}/Jamly.app"
  EOS
end
