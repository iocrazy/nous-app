/**
 * VideoAnalysisPanel Component - Display AI analysis results for videos
 */

import React, { useState, useEffect } from 'react';
import {
  Sparkles,
  Tag,
  Eye,
  Image,
  Loader2,
  RefreshCw,
  CheckCircle,
  AlertCircle,
  ChevronDown,
  ChevronUp,
  Layers,
  Camera,
  FileText,
} from 'lucide-react';
import {
  getVideoAnalysis,
  triggerAnalysis,
  suggestTags,
  VideoAnalysis,
  AnalysisStatus,
} from '../services/analysisService';

interface VideoAnalysisPanelProps {
  videoId: number;
  awemeId: string;
  onTagsSuggested?: (tags: string[]) => void;
  compact?: boolean;
}

export const VideoAnalysisPanel: React.FC<VideoAnalysisPanelProps> = ({
  videoId,
  awemeId,
  onTagsSuggested,
  compact = false,
}) => {
  const [analysis, setAnalysis] = useState<VideoAnalysis | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isExpanded, setIsExpanded] = useState(!compact);

  // Load analysis
  useEffect(() => {
    loadAnalysis();
  }, [videoId]);

  const loadAnalysis = async () => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await getVideoAnalysis(videoId);
      setAnalysis(data);
    } catch (err) {
      // No analysis yet is not an error
      setAnalysis(null);
    } finally {
      setIsLoading(false);
    }
  };

  const handleTriggerAnalysis = async () => {
    setIsAnalyzing(true);
    setError(null);
    try {
      const result = await triggerAnalysis(videoId);
      if (result.status === 'completed') {
        // Reload analysis
        await loadAnalysis();
      } else if (result.status === 'failed') {
        setError('Analysis failed. Please try again.');
      } else {
        // Pending - show message
        setError('Analysis queued. Please check back later.');
      }
    } catch (err) {
      setError('Failed to start analysis');
    } finally {
      setIsAnalyzing(false);
    }
  };

  const handleApplySuggestedTags = () => {
    if (analysis?.suggested_tags && onTagsSuggested) {
      onTagsSuggested(analysis.suggested_tags);
    }
  };

  if (isLoading) {
    return (
      <div className="p-4 bg-zinc-900 rounded-xl border border-zinc-800">
        <div className="flex items-center justify-center gap-2 text-zinc-500">
          <Loader2 size={16} className="animate-spin" />
          <span className="text-sm">Loading analysis...</span>
        </div>
      </div>
    );
  }

  // Compact view (for sidebar or card)
  if (compact) {
    return (
      <div className="bg-zinc-900 rounded-xl border border-zinc-800 overflow-hidden">
        <button
          onClick={() => setIsExpanded(!isExpanded)}
          className="w-full flex items-center justify-between p-3 hover:bg-zinc-800/50 transition-colors"
        >
          <div className="flex items-center gap-2">
            <Sparkles size={16} className="text-indigo-400" />
            <span className="text-sm font-medium text-white">AI Analysis</span>
            {analysis?.analyzed_at && (
              <CheckCircle size={14} className="text-emerald-400" />
            )}
          </div>
          {isExpanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
        </button>

        {isExpanded && (
          <div className="p-3 pt-0 space-y-3">
            {!analysis ? (
              <div className="text-center py-4">
                <p className="text-sm text-zinc-500 mb-3">No analysis available</p>
                <button
                  onClick={handleTriggerAnalysis}
                  disabled={isAnalyzing}
                  className="inline-flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white text-sm rounded-lg transition-colors disabled:opacity-50"
                >
                  {isAnalyzing ? (
                    <Loader2 size={14} className="animate-spin" />
                  ) : (
                    <Sparkles size={14} />
                  )}
                  Analyze Video
                </button>
              </div>
            ) : (
              <>
                {/* Suggested Tags */}
                {analysis.suggested_tags.length > 0 && (
                  <div>
                    <div className="flex items-center gap-2 mb-2">
                      <Tag size={12} className="text-emerald-400" />
                      <span className="text-xs text-zinc-500">Suggested Tags</span>
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {analysis.suggested_tags.slice(0, 5).map((tag, i) => (
                        <span
                          key={i}
                          className="px-2 py-0.5 text-xs bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 rounded-full"
                        >
                          {tag}
                        </span>
                      ))}
                    </div>
                    {onTagsSuggested && (
                      <button
                        onClick={handleApplySuggestedTags}
                        className="mt-2 text-xs text-indigo-400 hover:text-indigo-300"
                      >
                        Apply suggested tags
                      </button>
                    )}
                  </div>
                )}

                {/* Scene Description */}
                {analysis.scene_description && (
                  <div>
                    <div className="flex items-center gap-2 mb-1">
                      <Camera size={12} className="text-amber-400" />
                      <span className="text-xs text-zinc-500">Scene</span>
                    </div>
                    <p className="text-xs text-zinc-400 line-clamp-2">
                      {analysis.scene_description}
                    </p>
                  </div>
                )}
              </>
            )}
          </div>
        )}
      </div>
    );
  }

  // Full view
  return (
    <div className="bg-zinc-900 rounded-xl border border-zinc-800 overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between p-4 border-b border-zinc-800">
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-lg bg-indigo-500/10">
            <Sparkles size={18} className="text-indigo-400" />
          </div>
          <div>
            <h3 className="text-sm font-semibold text-white">AI Visual Analysis</h3>
            <p className="text-xs text-zinc-500">
              {analysis?.analyzed_at
                ? `Analyzed ${new Date(analysis.analyzed_at).toLocaleDateString()}`
                : 'Not analyzed yet'}
            </p>
          </div>
        </div>
        <button
          onClick={analysis ? loadAnalysis : handleTriggerAnalysis}
          disabled={isAnalyzing}
          className="flex items-center gap-2 px-3 py-1.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded-lg text-sm transition-colors"
        >
          {isAnalyzing ? (
            <Loader2 size={14} className="animate-spin" />
          ) : (
            <RefreshCw size={14} />
          )}
          {analysis ? 'Refresh' : 'Analyze'}
        </button>
      </div>

      {/* Content */}
      {error && (
        <div className="p-4 bg-red-500/10 border-b border-red-500/20">
          <div className="flex items-center gap-2 text-red-400 text-sm">
            <AlertCircle size={14} />
            {error}
          </div>
        </div>
      )}

      {!analysis ? (
        <div className="p-8 text-center">
          <div className="w-16 h-16 mx-auto mb-4 rounded-full bg-zinc-800 flex items-center justify-center">
            <Eye size={32} className="text-zinc-600" />
          </div>
          <p className="text-zinc-400 mb-4">
            AI analysis can identify objects, scenes, and suggest tags for this video.
          </p>
          <button
            onClick={handleTriggerAnalysis}
            disabled={isAnalyzing}
            className="inline-flex items-center gap-2 px-6 py-3 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg font-medium transition-colors disabled:opacity-50"
          >
            {isAnalyzing ? (
              <Loader2 size={18} className="animate-spin" />
            ) : (
              <Sparkles size={18} />
            )}
            Analyze This Video
          </button>
        </div>
      ) : (
        <div className="p-4 space-y-6">
          {/* Visual Analysis */}
          {analysis.visual_analysis && (
            <AnalysisSection
              icon={Eye}
              title="Visual Analysis"
              color="text-indigo-400"
            >
              <p className="text-sm text-zinc-300 leading-relaxed">
                {analysis.visual_analysis}
              </p>
            </AnalysisSection>
          )}

          {/* Scene Description */}
          {analysis.scene_description && (
            <AnalysisSection
              icon={Camera}
              title="Scene Description"
              color="text-amber-400"
            >
              <p className="text-sm text-zinc-300 leading-relaxed">
                {analysis.scene_description}
              </p>
            </AnalysisSection>
          )}

          {/* Content Categories */}
          {analysis.content_categories.length > 0 && (
            <AnalysisSection
              icon={Layers}
              title="Content Categories"
              color="text-purple-400"
            >
              <div className="flex flex-wrap gap-2">
                {analysis.content_categories.map((category, i) => (
                  <span
                    key={i}
                    className="px-3 py-1 text-sm bg-purple-500/10 text-purple-400 border border-purple-500/20 rounded-lg"
                  >
                    {category}
                  </span>
                ))}
              </div>
            </AnalysisSection>
          )}

          {/* Detected Objects */}
          {analysis.detected_objects.length > 0 && (
            <AnalysisSection
              icon={Image}
              title="Detected Objects"
              color="text-cyan-400"
            >
              <div className="flex flex-wrap gap-2">
                {analysis.detected_objects.map((obj, i) => (
                  <span
                    key={i}
                    className="px-2.5 py-1 text-xs bg-cyan-500/10 text-cyan-400 border border-cyan-500/20 rounded-full"
                  >
                    {obj}
                  </span>
                ))}
              </div>
            </AnalysisSection>
          )}

          {/* Suggested Tags */}
          {analysis.suggested_tags.length > 0 && (
            <AnalysisSection
              icon={Tag}
              title="Suggested Tags"
              color="text-emerald-400"
              action={
                onTagsSuggested && (
                  <button
                    onClick={handleApplySuggestedTags}
                    className="text-xs text-emerald-400 hover:text-emerald-300 transition-colors"
                  >
                    Apply All
                  </button>
                )
              }
            >
              <div className="flex flex-wrap gap-2">
                {analysis.suggested_tags.map((tag, i) => (
                  <span
                    key={i}
                    className="px-3 py-1 text-sm bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 rounded-lg cursor-pointer hover:bg-emerald-500/20 transition-colors"
                    onClick={() => onTagsSuggested?.([tag])}
                  >
                    #{tag}
                  </span>
                ))}
              </div>
            </AnalysisSection>
          )}
        </div>
      )}
    </div>
  );
};

// Analysis section component
interface AnalysisSectionProps {
  icon: React.ElementType;
  title: string;
  color: string;
  children: React.ReactNode;
  action?: React.ReactNode;
}

const AnalysisSection: React.FC<AnalysisSectionProps> = ({
  icon: Icon,
  title,
  color,
  children,
  action,
}) => (
  <div>
    <div className="flex items-center justify-between mb-3">
      <div className="flex items-center gap-2">
        <Icon size={16} className={color} />
        <h4 className="text-sm font-medium text-white">{title}</h4>
      </div>
      {action}
    </div>
    {children}
  </div>
);

export default VideoAnalysisPanel;
