
import React from 'react';
import {
  Sparkles, Download, BarChart3, Shield, Zap, Layers,
  ArrowRight, PlayCircle, CheckCircle2
} from 'lucide-react';
import packageJson from '../package.json';

interface LandingPageProps {
  onLoginClick: () => void;
  onGetStarted: () => void;
}

export const LandingPage: React.FC<LandingPageProps> = ({ onLoginClick, onGetStarted }) => {
  return (
    <div className="min-h-screen bg-black text-zinc-100 font-sans selection:bg-indigo-500/30 overflow-x-hidden">
      
      {/* Navbar */}
      <nav className="fixed top-0 left-0 right-0 z-50 border-b border-white/5 bg-black/50 backdrop-blur-xl">
        <div className="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 bg-gradient-to-br from-indigo-500 to-purple-600 rounded-lg flex items-center justify-center shadow-lg shadow-indigo-500/20">
              <Sparkles className="w-5 h-5 text-white" />
            </div>
            <span className="text-xl font-bold tracking-tight bg-clip-text text-transparent bg-gradient-to-r from-white to-zinc-400">
              MediaHub
            </span>
          </div>
          
          <div className="flex items-center gap-4">
            <button 
              onClick={onLoginClick}
              className="px-5 py-2 text-sm font-medium text-zinc-300 hover:text-white transition-colors"
            >
              Log in
            </button>
            <button 
              onClick={onGetStarted}
              className="px-5 py-2 text-sm font-medium bg-white text-black rounded-full hover:bg-zinc-200 transition-colors shadow-lg shadow-white/10"
            >
              Get Started
            </button>
          </div>
        </div>
      </nav>

      {/* Hero Section */}
      <section className="relative pt-32 pb-20 md:pt-48 md:pb-32 px-6">
        <div className="absolute top-0 left-1/2 -translate-x-1/2 w-[1000px] h-[500px] bg-indigo-500/20 rounded-full blur-[120px] -z-10 opacity-50 pointer-events-none" />
        
        <div className="max-w-4xl mx-auto text-center space-y-8">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-white/5 border border-white/10 text-xs font-medium text-indigo-300 mb-4 animate-in fade-in slide-in-from-bottom-4 duration-500">
            <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-indigo-400 opacity-75"></span>
              <span className="relative inline-flex rounded-full h-2 w-2 bg-indigo-500"></span>
            </span>
            v2.0 Now Available with Batch Processing
          </div>
          
          <h1 className="text-5xl md:text-7xl font-bold tracking-tight leading-tight animate-in fade-in slide-in-from-bottom-6 duration-700">
            The Ultimate <br />
            <span className="text-transparent bg-clip-text bg-gradient-to-r from-indigo-400 via-purple-400 to-pink-400">
              Media Manager
            </span>
          </h1>
          
          <p className="text-lg md:text-xl text-zinc-400 max-w-2xl mx-auto leading-relaxed animate-in fade-in slide-in-from-bottom-8 duration-700 delay-100">
            Parse, download, and organize content from social platforms with professional-grade tools. 
            Watermark-free downloads, batch processing, and detailed analytics.
          </p>

          <div className="flex flex-col md:flex-row items-center justify-center gap-4 pt-4 animate-in fade-in slide-in-from-bottom-8 duration-700 delay-200">
            <button 
              onClick={onGetStarted}
              className="w-full md:w-auto px-8 py-4 bg-indigo-600 hover:bg-indigo-500 text-white rounded-xl font-semibold transition-all shadow-xl shadow-indigo-500/20 flex items-center justify-center gap-2 group"
            >
              Start for free
              <ArrowRight className="w-4 h-4 group-hover:translate-x-1 transition-transform" />
            </button>
            <button className="w-full md:w-auto px-8 py-4 bg-zinc-900 hover:bg-zinc-800 text-zinc-200 border border-zinc-800 rounded-xl font-semibold transition-all flex items-center justify-center gap-2">
              <PlayCircle className="w-4 h-4" />
              Watch Demo
            </button>
          </div>
        </div>
      </section>

      {/* Feature Grid */}
      <section className="py-20 bg-zinc-950/50 border-t border-white/5">
        <div className="max-w-7xl mx-auto px-6">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
            <FeatureCard 
              icon={Download}
              title="Smart Parser"
              desc="Extract high-quality video, audio, and images without watermarks instantly."
            />
            <FeatureCard 
              icon={Layers}
              title="Batch Processing"
              desc="Import hundreds of links at once and let our automated queue handle the rest."
            />
            <FeatureCard 
              icon={BarChart3}
              title="Deep Analytics"
              desc="Track your collection habits with interactive charts and data insights."
            />
          </div>
        </div>
      </section>

      {/* Preview Section */}
      <section className="py-24 px-6 relative overflow-hidden">
        <div className="max-w-6xl mx-auto bg-zinc-900 border border-zinc-800 rounded-2xl p-2 md:p-4 shadow-2xl relative z-10">
           <div className="absolute top-0 left-0 w-full h-full bg-gradient-to-b from-indigo-500/10 to-transparent pointer-events-none rounded-2xl" />
           <div className="bg-black rounded-xl border border-zinc-800 overflow-hidden aspect-video relative flex items-center justify-center group cursor-default">
              {/* Mock UI Representation */}
              <div className="text-center space-y-4 opacity-50 group-hover:opacity-100 transition-opacity">
                 <div className="w-16 h-16 bg-zinc-800 rounded-full mx-auto flex items-center justify-center mb-4">
                    <PlayCircle className="w-8 h-8 text-zinc-400" />
                 </div>
                 <p className="text-zinc-500 font-mono text-sm">Interactive Dashboard Preview</p>
              </div>
           </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="py-12 border-t border-white/5 bg-black">
        <div className="max-w-7xl mx-auto px-6 flex flex-col md:flex-row justify-between items-center gap-6">
          <div className="text-zinc-500 text-sm flex items-center gap-3">
            <span>© 2026 MediaHub. All rights reserved.</span>
            <span className="px-2 py-0.5 bg-zinc-800 rounded text-xs font-mono text-zinc-400">
              v{packageJson.version}
            </span>
          </div>
          <div className="flex gap-6 text-zinc-500 text-sm">
            <a href="#" className="hover:text-zinc-300">Privacy</a>
            <a href="#" className="hover:text-zinc-300">Terms</a>
            <a href="#" className="hover:text-zinc-300">Twitter</a>
          </div>
        </div>
      </footer>
    </div>
  );
};

const FeatureCard = ({ icon: Icon, title, desc }: { icon: any, title: string, desc: string }) => (
  <div className="p-8 rounded-2xl bg-zinc-900/50 border border-white/5 hover:border-indigo-500/30 hover:bg-zinc-900 transition-all group">
    <div className="w-12 h-12 bg-zinc-800 rounded-xl flex items-center justify-center mb-6 group-hover:bg-indigo-500/20 group-hover:text-indigo-400 transition-colors">
      <Icon className="w-6 h-6 text-zinc-400 group-hover:text-indigo-400" />
    </div>
    <h3 className="text-xl font-semibold text-zinc-100 mb-3">{title}</h3>
    <p className="text-zinc-400 leading-relaxed">{desc}</p>
  </div>
);
