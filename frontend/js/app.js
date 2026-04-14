/**
 * Hive 🐝 — shared frontend utilities.
 */

// ---- Auth helpers ----
function getToken() {
    return localStorage.getItem('token');
}

function authHeaders() {
    const token = getToken();
    return token ? { 'Authorization': `Bearer ${token}` } : {};
}

function isAuthenticated() {
    return !!getToken();
}

function logout() {
    localStorage.removeItem('token');
    // Fire-and-forget: clear the httpOnly refresh cookie server-side
    fetch('/api/auth/logout', { method: 'POST', credentials: 'include' }).catch(() => {});
    window.location.href = '/login';
}

// ---- Token refresh ----
let _refreshing = null; // single in-flight refresh promise to avoid race conditions

async function refreshAccessToken() {
    // Deduplicate: if a refresh is already in flight, wait for it
    if (_refreshing) return _refreshing;

    _refreshing = (async () => {
        try {
            const res = await fetch('/api/auth/refresh', {
                method: 'POST',
                credentials: 'include', // send the httpOnly refresh cookie
            });
            if (res.ok) {
                const data = await res.json();
                localStorage.setItem('token', data.access_token);
                return true;
            }
            return false;
        } catch (_) {
            return false;
        } finally {
            _refreshing = null;
        }
    })();

    return _refreshing;
}

// ---- API helpers ----
async function apiFetch(path, options = {}, _isRetry = false) {
    const headers = { ...authHeaders(), ...(options.headers || {}) };
    const res = await fetch(path, { ...options, headers, credentials: 'include' });

    if (res.status === 401 && !_isRetry) {
        const refreshed = await refreshAccessToken();
        if (refreshed) {
            // Retry once with the new access token
            return apiFetch(path, options, true);
        }
        // Refresh failed — session is dead, redirect to login
        logout();
        return res; // unreachable after redirect, but keeps return type consistent
    }

    return res;
}

// ---- Formatting helpers ----
function timeAgo(dateString) {
    if (!dateString) return 'Never';
    const date = new Date(dateString);
    const now = new Date();
    const seconds = Math.floor((now - date) / 1000);

    if (seconds < 60) return 'Just now';
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
    return `${Math.floor(seconds / 86400)}d ago`;
}

function statusBadgeClass(status) {
    const classes = {
        'active': 'bg-green-100 text-green-800',
        'idle': 'bg-yellow-100 text-yellow-800',
        'offline': 'bg-gray-100 text-gray-800',
        'pending': 'bg-blue-100 text-blue-800',
        'error': 'bg-red-100 text-red-800',
    };
    return `px-2 py-1 text-xs rounded-full font-medium ${classes[status] || classes.offline}`;
}
