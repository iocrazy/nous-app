// frontend/components/DownloadProgress/index.tsx

import React from 'react';
import { DownloadProgressProps, ProgressStyleType } from './types';
import { NeonBorder } from './NeonBorder';
import { WaveLiquid } from './WaveLiquid';
import { SimpleBar } from './SimpleBar';

interface DownloadProgressComponentProps extends DownloadProgressProps {
  /** Progress bar style */
  style?: ProgressStyleType;
}

export const DownloadProgress: React.FC<DownloadProgressComponentProps> = ({
  style = 'neon',
  size = 'normal',
  ...props
}) => {
  // Mini size always uses SimpleBar for space efficiency
  if (size === 'mini') {
    return <SimpleBar size="mini" {...props} />;
  }

  // Normal size uses selected style
  switch (style) {
    case 'wave':
      return <WaveLiquid size="normal" {...props} />;
    case 'neon':
    default:
      return <NeonBorder size="normal" {...props} />;
  }
};

// Re-export types and individual components
export * from './types';
export { NeonBorder } from './NeonBorder';
export { WaveLiquid } from './WaveLiquid';
export { SimpleBar } from './SimpleBar';

export default DownloadProgress;
