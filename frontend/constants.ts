import { ParsedMedia, DownloadStatus } from './types';

// NOTE: In a real app, these would come from your backend API
export const MOCK_PARSED_DATA: ParsedMedia = {
  platform_id: "732918237123",
  like_count: 125043,
  comment_count: 4320,
  share_count: 8901,
  favorite_count: 33201,
  original_url: "https://v.douyin.com/example",
  duration: "45",
  resolution: "1080p",
  datasize: "15400000",
  hashtags: "#cyberpunk #tech #react",
  published_at: new Date().toISOString(),
  author: "TechHunter",
  title: "Building the Future with React & Gemini",
  media_type: "video",
  description: "Check out this amazing new feature we built! #coding",
  need_download_video: true,
  source_platform: "douyin",
  // Using the first provided test URL
  video_download_urls: ["https://pichome.heygo.cn:88/index.php?mod=io&op=getStream&path=ejRPRDVoMU5EM1lXRWVHMF8yUjVVVV95aFJxb0Q3LVdVQ2xqYk5NVklBajZ5YjNqeERpOWY4RExXRDJIZVdZRkRLeVFBNklYRFUzd3FweUQ"],
  image_download_urls: [],
  music_download_urls: ["#"],
  music_name: "Original Sound - TechHunter",
  need_download_music: true,
  video_download_status: DownloadStatus.COMPLETED,
  music_download_status: DownloadStatus.PENDING,
  notes: "Check this out for the UI design inspiration.",
  tags: ["UI", "React", "Design"]
};

export const MOCK_LIBRARY: Video[] = [
  MOCK_PARSED_DATA,
  {
    ...MOCK_PARSED_DATA,
    platform_id: "732918237124",
    title: "Morning Vibes in Tokyo",
    author: "TravelDaily",
    like_count: 8500,
    media_type: "carousel",
    image_download_urls: ["https://picsum.photos/400/600", "https://picsum.photos/400/601"],
    video_download_urls: [],
    notes: "Possible background for the landing page.",
    tags: ["Travel", "Japan", "Photography"]
  },
  {
    ...MOCK_PARSED_DATA,
    platform_id: "732918237125",
    title: "Cooking Masterclass: Steak",
    author: "ChefRamsayClone",
    like_count: 994000,
    media_type: "video",
    // Using the third provided test URL (different video)
    video_download_urls: ["https://pichome.heygo.cn:88/index.php?mod=io&op=getStream&path=ejRPRDVoMU5EM1lXRWV6ai16QnpWRXloaTB6NUNMLVZEU2huYnROQ0pBajV5T2prd0QyeUpaZk9YanFIS2p0VERfbVlWNmRGWEVIMC01eUQ"],
    notes: "",
    tags: ["Food", "Cooking"]
  }
];
