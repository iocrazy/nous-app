import React, { useState, useRef, useEffect } from 'react';
import { ChevronDown, Check, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { ReviewStatus } from '../types';

interface ReviewStatusDropdownProps {
  currentStatus: ReviewStatus | null;
  onStatusChange: (status: ReviewStatus | null) => void;
}

export const STATUS_CONFIG: Record<ReviewStatus, { color: string; bgColor: string; label: string }> = {
  pending_review: { color: 'bg-yellow-400', bgColor: 'bg-yellow-400/10 text-yellow-300', label: 'mediatrack.review.pendingReview' },
  in_review: { color: 'bg-blue-400', bgColor: 'bg-blue-400/10 text-blue-300', label: 'mediatrack.review.inReview' },
  feedback_collected: { color: 'bg-orange-400', bgColor: 'bg-orange-400/10 text-orange-300', label: 'mediatrack.review.feedbackCollected' },
  approved: { color: 'bg-green-400', bgColor: 'bg-green-400/10 text-green-300', label: 'mediatrack.review.approved' },
};

const STATUS_ORDER: ReviewStatus[] = ['pending_review', 'in_review', 'feedback_collected', 'approved'];

export const ReviewStatusDropdown: React.FC<ReviewStatusDropdownProps> = ({ currentStatus, onStatusChange }) => {
  const { t } = useTranslation();
  const [isOpen, setIsOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    };

    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }

    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [isOpen]);

  const handleSelect = (status: ReviewStatus | null) => {
    onStatusChange(status);
    setIsOpen(false);
  };

  return (
    <div ref={containerRef} className="relative">
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-ink-800 hover:bg-ink-700 border border-ink-700 text-sm transition-colors"
      >
        {currentStatus ? (
          <>
            <span className={`w-2 h-2 rounded-full ${STATUS_CONFIG[currentStatus].color}`} />
            <span className="text-ink-200">{t(STATUS_CONFIG[currentStatus].label)}</span>
          </>
        ) : (
          <span className="text-ink-400">{t('mediatrack.review.setStatus')}</span>
        )}
        <ChevronDown className={`w-3.5 h-3.5 text-ink-400 transition-transform ${isOpen ? 'rotate-180' : ''}`} />
      </button>

      {isOpen && (
        <div className="absolute top-full left-0 z-50 mt-1 w-56 rounded-[8px] border border-ink-700 bg-card p-1 shadow-[0_12px_34px_rgba(0,0,0,0.22)]">
          {STATUS_ORDER.map((status) => {
            const config = STATUS_CONFIG[status];
            const isActive = currentStatus === status;

            return (
              <button
                key={status}
                onClick={() => handleSelect(status)}
                className={`w-full flex items-center gap-2.5 rounded-[6px] px-3 py-2 text-sm transition-colors text-left ${
                  isActive
                    ? 'bg-[color-mix(in_srgb,var(--accent)_13%,transparent)]'
                    : 'hover:bg-ink-700'
                }`}
              >
                <span className={`w-2 h-2 rounded-full flex-shrink-0 ${config.color}`} />
                <span className="text-content flex-1">{t(config.label)}</span>
                {isActive && <Check className="w-4 h-4 text-[color:var(--accent-text)] flex-shrink-0" />}
              </button>
            );
          })}

          <div className="border-t border-ink-700 my-1" />

          <button
            onClick={() => handleSelect(null)}
            className="w-full flex items-center gap-2.5 rounded-[6px] px-3 py-2 text-sm hover:bg-ink-700 transition-colors text-left"
          >
            <X className="w-3.5 h-3.5 text-content-3 flex-shrink-0" />
            <span className="text-content-3 flex-1">{t('mediatrack.review.removeStatus')}</span>
          </button>
        </div>
      )}
    </div>
  );
};

export default ReviewStatusDropdown;
