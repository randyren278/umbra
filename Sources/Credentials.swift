import AppKit
import AuthenticationServices
import Security

enum BrowserCredentials {
    private static let entitled: Bool = {
        var code: SecCode?, signed: SecStaticCode?, information: CFDictionary?
        guard SecCodeCopySelf([], &code) == errSecSuccess, let code,
              SecCodeCopyStaticCode(code, [], &signed) == errSecSuccess, let signed,
              SecCodeCopySigningInformation(signed, SecCSFlags(rawValue: kSecCSSigningInformation), &information) == errSecSuccess,
              let values = information as? [String: Any],
              let entitlements = values[kSecCodeInfoEntitlementsDict as String] as? [String: Any] else { return false }
        return entitlements["com.apple.developer.web-browser.public-key-credential"] as? Bool == true
    }()
    private static let manager = ASAuthorizationWebBrowserPublicKeyCredentialManager()

    static func show(in window: NSWindow, site: String) {
        guard window.attachedSheet == nil, let content = window.contentView else { return }
        let alert = NSAlert()
        alert.messageText = "Passwords & Passkeys"
        let passwords = NSWorkspace.shared.urlForApplication(withBundleIdentifier: "com.apple.Passwords")
        var text = passwords != nil
            ? "For \(site), open Passwords, unlock it, and copy your username or password. Return here, select the matching field, and press ⌘V."
            : "For \(site), find your saved login in System Settings. Return here, select the matching field, and press ⌘V. The Passwords app requires macOS 15 or later."
        let canRequest: Bool
        if entitled {
            switch manager.authorizationStateForPlatformCredentials {
            case .authorized:
                text += "\n\nPasskeys are allowed. Use this website’s passkey sign-in button."
                canRequest = false
            case .notDetermined:
                text += "\n\nAllow Umbra to use your passkeys, then use this website’s passkey sign-in button."
                canRequest = true
            case .denied:
                text += "\n\nPasskey access is off for Umbra. Review access in AutoFill & Passwords settings."
                canRequest = false
            @unknown default:
                text += "\n\nPasskey access is unavailable. Review AutoFill & Passwords settings."
                canRequest = false
            }
        } else {
            text += "\n\nLocal passkeys require an Apple-approved browser build of Umbra. This build supports signing in with a password. Safari sign-ins stay in Safari."
            canRequest = false
        }
        alert.informativeText = text
        alert.addButton(withTitle: "Open Passwords").isEnabled = passwords != nil
        alert.addButton(withTitle: "Cancel")
        alert.addButton(withTitle: canRequest ? "Allow Passkeys" : "AutoFill Settings")
        if unseen { alert.window.alphaValue = 0; alert.window.ignoresMouseEvents = true }
        alert.beginSheetModal(for: window) { [weak window, weak content] response in
            guard let window, let content, window.contentView === content else { return }
            switch response {
            case .alertFirstButtonReturn:
                guard let passwords else { return }
                NSWorkspace.shared.openApplication(at: passwords, configuration: .init()) { _, error in
                    if error != nil { DispatchQueue.main.async {
                        guard window.contentView === content else { return }
                        failed(in: window, message: "Passwords couldn’t open. Open it from Applications and copy your login, then return here and paste it.")
                    } }
                }
            case .alertThirdButtonReturn:
                if canRequest {
                    manager.requestAuthorizationForPublicKeyCredentials { state in
                        DispatchQueue.main.async {
                            guard window.isVisible, window.contentView === content else { return }
                            if state == .authorized { show(in: window, site: site) }
                            else { failed(in: window, message: "Passkey access wasn’t granted. You can sign in with a password or review access in AutoFill & Passwords settings.") }
                        }
                    }
                } else {
                    ASSettingsHelper.openCredentialProviderAppSettings { error in
                        if error != nil { DispatchQueue.main.async {
                            guard window.contentView === content else { return }
                            failed(in: window, message: "Settings couldn’t open. Open System Settings and search for AutoFill & Passwords.")
                        } }
                    }
                }
            default: break
            }
        }
    }

    private static func failed(in window: NSWindow, message: String) {
        guard window.isVisible, window.attachedSheet == nil else { return }
        let alert = NSAlert()
        alert.messageText = "Passwords & Passkeys"
        alert.informativeText = message
        alert.addButton(withTitle: "OK")
        if unseen { alert.window.alphaValue = 0; alert.window.ignoresMouseEvents = true }
        alert.beginSheetModal(for: window)
    }
}
