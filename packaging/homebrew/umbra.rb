# Homebrew formula for Umbra, built from the main branch with swiftc (head only: no releases, no bottles).
#
# Homebrew installs formulae from taps. A tap is a GitHub repo named homebrew-<name> with formulae
# in Formula/; to publish this one, copy it to randyren278/homebrew-tap as Formula/umbra.rb. Users
# then run
#   brew install --HEAD randyren278/tap/umbra
# and `brew upgrade --fetch-HEAD umbra` to rebuild from the latest main.
class Umbra < Formula
  desc "Headless WebKit browser for macOS background jobs"
  homepage "https://github.com/randyren278/macOS-headless-browser"
  license "MIT"
  head "https://github.com/randyren278/macOS-headless-browser.git", branch: "main"

  depends_on macos: :sonoma

  def install
    # Builds and signs build/Umbra.app as com.randyren.umbra, the identifier its cookie store is
    # keyed by, so this install shares the store of one made with `make install`.
    system "make", "build"
    prefix.install "build/Umbra.app"
    bin.install_symlink prefix/"Umbra.app/Contents/MacOS/umbra"
  end

  test do
    assert_match "umbra ", shell_output("#{bin}/umbra --version")
  end
end
