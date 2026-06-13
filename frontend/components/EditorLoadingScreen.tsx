import { useState, useEffect } from 'react';

interface EditorLoadingScreenProps {
  visible: boolean;
}

export function EditorLoadingScreen({ visible }: EditorLoadingScreenProps) {
  // Track whether the overlay should remain in the DOM (for fade-out animation)
  const [mounted, setMounted] = useState(visible);
  const [fading, setFading] = useState(false);

  useEffect(() => {
    if (visible) {
      setMounted(true);
      setFading(false);
    } else {
      // Trigger fade-out, then unmount after transition completes
      setFading(true);
      const timer = setTimeout(() => setMounted(false), 500);
      return () => clearTimeout(timer);
    }
  }, [visible]);

  if (!mounted) return null;

  return (
    <>
      {/* Inject keyframe animation once */}
      <style>{`
        @keyframes editor-loading-bar {
          0%   { transform: translateX(-100%); }
          50%  { transform: translateX(250%); }
          100% { transform: translateX(-100%); }
        }
        .editor-loading-bar-inner {
          animation: editor-loading-bar 1.6s ease-in-out infinite;
          width: 40%;
        }
      `}</style>

      <div
        className={`fixed inset-0 z-50 bg-ink-950 flex flex-col items-center justify-center gap-0 transition-opacity duration-500 ${
          fading ? 'opacity-0 pointer-events-none' : 'opacity-100'
        }`}
      >
        {/* Logo */}
        <svg
          width="56"
          height="56"
          viewBox="0 0 24 24"
          fill="none"
          className="text-indigo-500 mb-3"
        >
          <path
            d="M12 2L9.5 9.5H2L7.75 13.75L5.5 21L12 16.75L18.5 21L16.25 13.75L22 9.5H14.5L12 2Z"
            fill="currentColor"
          />
        </svg>

        {/* Brand name */}
        <span className="text-xs text-ink-600 mb-6 tracking-widest uppercase">MediaHub</span>

        {/* Progress bar */}
        <div className="w-64 h-1 bg-ink-800 rounded-full overflow-hidden">
          <div className="editor-loading-bar-inner h-full bg-indigo-500 rounded-full" />
        </div>
      </div>
    </>
  );
}
