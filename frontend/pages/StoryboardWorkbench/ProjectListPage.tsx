import React from 'react';

export function ProjectListPage() {
  return (
    <div className="flex flex-col h-full p-6">
      <h1 className="text-2xl font-bold text-gray-900 dark:text-white mb-6">
        Storyboard Workbench
      </h1>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
        {/* Project cards will be rendered here */}
      </div>
    </div>
  );
}
