// Avoid duplicate injection
if (!window.__mediahubToastInjected) {
  window.__mediahubToastInjected = true;

  chrome.runtime.onMessage.addListener((msg) => {
    if (msg.action === 'showToast') {
      showToast(msg.message, msg.type);
    }
  });
}

function showToast(message, type) {
  // Remove existing toast if any
  const existing = document.getElementById('mediahub-toast');
  if (existing) existing.remove();

  const toast = document.createElement('div');
  toast.id = 'mediahub-toast';

  const colors = {
    success: { bg: '#1b5e20', border: '#4caf50' },
    error: { bg: '#b71c1c', border: '#f44336' },
    info: { bg: '#0d47a1', border: '#2196f3' },
  };
  const c = colors[type] || colors.info;

  Object.assign(toast.style, {
    position: 'fixed',
    top: '16px',
    right: '16px',
    zIndex: '2147483647',
    padding: '12px 20px',
    borderRadius: '8px',
    background: c.bg,
    border: `1px solid ${c.border}`,
    color: '#ffffff',
    fontSize: '14px',
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
    boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
    transition: 'opacity 0.3s ease',
    opacity: '0',
    maxWidth: '360px',
    wordBreak: 'break-word',
  });

  toast.textContent = message;
  document.body.appendChild(toast);

  // Fade in
  requestAnimationFrame(() => {
    toast.style.opacity = '1';
  });

  // Auto dismiss
  const delay = type === 'error' ? 5000 : 3000;
  setTimeout(() => {
    toast.style.opacity = '0';
    setTimeout(() => toast.remove(), 300);
  }, delay);
}
