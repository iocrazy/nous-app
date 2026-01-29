import { DouyinBase, DownloadStatus } from './types';

// NOTE: In a real app, these would come from your backend API
export const MOCK_PARSED_DATA: DouyinBase = {
  aweme_id: "732918237123",
  video_digg_count: 125043,
  video_comment_count: 4320,
  video_share_count: 8901,
  video_collect_count: 33201,
  video_original_url: "https://v.douyin.com/example",
  video_duration: "45",
  video_resolution: "1080p",
  video_datasize: "15400000",
  video_hashtag_name: "#cyberpunk #tech #react",
  video_created_time: new Date().toISOString(),
  author: "TechHunter",
  video_title: "Building the Future with React & Gemini",
  aweme_type: "video",
  video_desc: "Check out this amazing new feature we built! #coding",
  need_download_video: true,
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

export const MOCK_LIBRARY: DouyinBase[] = [
  MOCK_PARSED_DATA,
  {
    ...MOCK_PARSED_DATA,
    aweme_id: "732918237124",
    video_title: "Morning Vibes in Tokyo",
    author: "TravelDaily",
    video_digg_count: 8500,
    aweme_type: "image_album", // Example of image collection
    image_download_urls: ["https://picsum.photos/400/600", "https://picsum.photos/400/601"],
    video_download_urls: [],
    notes: "Possible background for the landing page.",
    tags: ["Travel", "Japan", "Photography"]
  },
  {
    ...MOCK_PARSED_DATA,
    aweme_id: "732918237125",
    video_title: "Cooking Masterclass: Steak",
    author: "ChefRamsayClone",
    video_digg_count: 994000,
    aweme_type: "video",
    // Using the third provided test URL (different video)
    video_download_urls: ["https://pichome.heygo.cn:88/index.php?mod=io&op=getStream&path=ejRPRDVoMU5EM1lXRWV6ai16QnpWRXloaTB6NUNMLVZEU2huYnROQ0pBajV5T2prd0QyeUpaZk9YanFIS2p0VERfbVlWNmRGWEVIMC01eUQ"],
    notes: "",
    tags: ["Food", "Cooking"]
  }
];