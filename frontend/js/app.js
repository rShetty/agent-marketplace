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
    window.location.href = '/';
}

// ---- API helpers ----
async function apiFetch(path, options = {}) {
    const headers = { ...authHeaders(), ...(options.headers || {}) };
    const res = await fetch(path, { ...options, headers });
    if (res.status === 401) {
        logout();
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
