/**
 * Login page. The Google/GitHub buttons are plain links to Local's own /auth/{provider}
 * (P07: Cloud is the sole OAuth authority - Local just redirects there, no client-side OAuth URL
 * building or CSRF-state handling happens here any more; Cloud generates and validates that
 * state itself). This script only shows a notification for the auth_result query param Local's
 * /auth/callback redirects back here with on failure.
 */
class LoginUtilities {
    static parseURLParameters() {
        const urlParams = new URLSearchParams(window.location.search);
        return {
            authResult: urlParams.get('auth_result'),
            errorMessage: urlParams.get('error_message'),
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

    init() {
        this.handleAuthResults();
    }
}

document.addEventListener('DOMContentLoaded', function () {
    new LoginHandler().init();
});
