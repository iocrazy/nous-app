import React from 'react';
import { Sparkles, Upload, ArrowLeft } from 'lucide-react';

interface WelcomeScreenProps {
  onImport: () => void;
  onCreateStory: () => void;
  onBack: () => void;
}

const WelcomeScreen: React.FC<WelcomeScreenProps> = ({
  onImport,
  onCreateStory,
  onBack,
}) => {
  return (
    <div className="flex items-center justify-center min-h-[60vh]">
      <div className="w-full max-w-md">
        {/* Header */}
        <div className="text-center mb-8">
          <div className="flex items-center justify-center gap-2 mb-4">
            <Sparkles className="w-6 h-6 text-amber-500" />
            <h1 className="text-lg font-semibold text-white">
              Welcome to Script Assistant
            </h1>
          </div>
          <p className="text-sm text-zinc-400">
            Choose how to start your script creation:
          </p>
        </div>

        {/* Import Card */}
        <button
          onClick={onImport}
          className="w-full mb-4 p-4 rounded-xl border border-zinc-700 bg-zinc-800/50 hover:bg-zinc-800 hover:border-zinc-600 cursor-pointer transition-all text-left"
        >
          <div className="flex items-start gap-3">
            <Upload className="w-5 h-5 text-amber-500 flex-shrink-0 mt-0.5" />
            <div>
              <h2 className="font-medium text-white mb-1">Import Script</h2>
              <p className="text-sm text-zinc-400">
                Import from TXT, PDF, Word files
              </p>
            </div>
          </div>
        </button>

        {/* Create Story Card */}
        <button
          onClick={onCreateStory}
          className="w-full mb-8 p-4 rounded-xl border border-zinc-700 bg-zinc-800/50 hover:bg-zinc-800 hover:border-zinc-600 cursor-pointer transition-all text-left"
        >
          <div className="flex items-start gap-3">
            <Sparkles className="w-5 h-5 text-indigo-500 flex-shrink-0 mt-0.5" />
            <div>
              <h2 className="font-medium text-white mb-1">Create Story</h2>
              <p className="text-sm text-zinc-400">
                Enter story concept, AI generates outline
              </p>
            </div>
          </div>
        </button>

        {/* Back Link */}
        <button
          onClick={onBack}
          className="flex items-center gap-2 text-sm text-zinc-500 hover:text-zinc-300 transition-colors"
        >
          <ArrowLeft className="w-4 h-4" />
          Back to project
        </button>
      </div>
    </div>
  );
};

export default WelcomeScreen;
