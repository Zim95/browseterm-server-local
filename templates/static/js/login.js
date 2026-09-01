/**
 * Login page. The Google/GitHub buttons are plain links to Local's own /auth/{provider}
 * (P07: Cloud is the sole OAuth authority - Local just redirects there, no client-side OAuth URL
 * building or CSRF-state handling happens here any more; Cloud generates and validates that
 * state itself). This script only shows a notification for the auth_result query param Local's
 * /auth/callback redirects back here with on failure.
 *
 * Desktop login (?target=desktop&desktop_port=<n>): browseterm-desktop opens THIS page directly
 * in the user's real system browser (Google/GitHub block OAuth from its own embedded WebView -
 * see api_handlers.py's auth_provider_redirect docstring) rather than showing it inside the
 * WebView. Whichever provider button the user actually clicks needs to carry target/desktop_port
 * through to /auth/{provider} (api_handlers.py reads them there to set the short-lived cookie
 * that survives the OAuth round trip) - plain static hrefs can't do that, so this appends them
 * from the CURRENT page's own URL onto both buttons on load. A normal browser/WebView visit to
 * /login (no desktop_port) leaves the buttons exactly as they already were.
 */
class LoginUtilities {
    static parseURLParameters() {
        const urlParams = new URLSearchParams(window.location.search);
        return {
            authResult: urlParams.get('auth_result'),
            errorMessage: urlParams.get('error_message'),
            target: urlParams.get('target'),
            desktopPort: urlParams.get('desktop_port'),
        };
    }

    static showNotification(type, title, message, duration) {
        if (typeof window.notifications === 'undefined' || window.notifications === null) {
            console.warn('Notification system not available');
            return;
        }
        if (typeof window.notifications[type] !== 'function') {
            console.warn('Notification type not available');
            return;
        }
        window.notifications[type](title, message, duration);
    }
}

class LoginHandler {
    handleAuthResults() {
        const { authResult, errorMessage } = LoginUtilities.parseURLParameters();

        if (!authResult) {
            LoginUtilities.showNotification('info', 'Welcome!', 'Choose your preferred login method below', 3000);
            return;
        }

        switch (authResult) {
            case 'error':
                LoginUtilities.showNotification('error', 'Login Failed', errorMessage || 'Authentication failed. Please try again.', 10000);
                break;
            case 'cancelled':
                LoginUtilities.showNotification('warning', 'Login Cancelled', 'You cancelled the authentication process', 4000);
                break;
            default:
                LoginUtilities.showNotification('error', 'Login Error', 'Unknown authentication result', 5000);
        }
    }

    /** Carries target=desktop&desktop_port=<n> from this page's own URL onto the provider
     * buttons, so a desktop login (opened directly in the system browser) survives the click. */
    threadDesktopParamsOntoProviderButtons() {
        const { target, desktopPort } = LoginUtilities.parseURLParameters();
        if (target !== 'desktop' || !desktopPort) {
            return;
        }
        document.querySelectorAll('a[href^="/auth/"]').forEach((link) => {
            const url = new URL(link.getAttribute('href'), window.location.origin);
            url.searchParams.set('target', target);
            url.searchParams.set('desktop_port', desktopPort);
            link.setAttribute('href', url.pathname + url.search);
        });
    }

    init() {
        this.handleAuthResults();
        this.threadDesktopParamsOntoProviderButtons();
    }
}

document.addEventListener('DOMContentLoaded', function () {
    new LoginHandler().init();
});

if (typeof module !== 'undefined' && module.exports) {
    module.exports = { LoginUtilities, LoginHandler };
}
