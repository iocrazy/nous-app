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
        className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 text-sm transition-colors"
      >
        {currentStatus ? (
          <>
            <span className={`w-2 h-2 rounded-full ${STATUS_CONFIG[currentStatus].color}`} />
            <span className="text-zinc-200">{t(STATUS_CONFIG[currentStatus].label)}</span>
          </>
        ) : (
          <span className="text-zinc-400">{t('mediatrack.review.setStatus')}</span>
        )}
        <ChevronDown className={`w-3.5 h-3.5 text-zinc-400 transition-transform ${isOpen ? 'rotate-180' : ''}`} />
      </button>

      {isOpen && (
        <div className="absolute top-full left-0 mt-1 w-56 rounded-lg bg-zinc-900 border border-zinc-700 shadow-xl z-50 py-1">
          {STATUS_ORDER.map((status) => {
            const config = STATUS_CONFIG[status];
            const isActive = currentStatus === status;

            return (
              <button
                key={status}
                onClick={() => handleSelect(status)}
                className="w-full flex items-center gap-2.5 px-3 py-2 text-sm hover:bg-zinc-800 transition-colors text-left"
              >
                <span className={`w-2 h-2 rounded-full flex-shrink-0 ${config.color}`} />
                <span className="text-zinc-200 flex-1">{t(config.label)}</span>
                {isActive && <Check className="w-4 h-4 text-zinc-400 flex-shrink-0" />}
              </button>
            );
          })}

          <div className="border-t border-zinc-700 my-1" />

          <button
            onClick={() => handleSelect(null)}
            className="w-full flex items-center gap-2.5 px-3 py-2 text-sm hover:bg-zinc-800 transition-colors text-left"
          >
            <X className="w-3.5 h-3.5 text-zinc-500 flex-shrink-0" />
            <span className="text-zinc-400 flex-1">{t('mediatrack.review.removeStatus')}</span>
          </button>
        </div>
      )}
    </div>
  );
};

export default ReviewStatusDropdown;
