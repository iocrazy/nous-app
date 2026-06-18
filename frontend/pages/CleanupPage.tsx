import { CleanupSuggestionsView } from '../components/CleanupSuggestionsView';

export function CleanupPage() {
  return (
    <div className="max-w-5xl mx-auto animate-in fade-in slide-in-from-bottom-4 duration-500">
      <div className="mb-8">
        <h1 className="text-lg font-semibold text-ink-100 mb-2">Storage Cleanup</h1>
        <p className="text-ink-400">Review and clean up videos to free up storage space</p>
      </div>
      <CleanupSuggestionsView />
    </div>
  );
}
