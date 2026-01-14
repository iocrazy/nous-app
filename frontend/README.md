
# DouyinMediaHub 🎬

![React](https://img.shields.io/badge/React-19.0-blue?style=for-the-badge&logo=react)
![TypeScript](https://img.shields.io/badge/TypeScript-5.0-blue?style=for-the-badge&logo=typescript)
![Tailwind CSS](https://img.shields.io/badge/Tailwind-3.0-38bdf8?style=for-the-badge&logo=tailwindcss)
![Supabase](https://img.shields.io/badge/Supabase-PostgreSQL-3ecf8e?style=for-the-badge&logo=supabase)
![Vite](https://img.shields.io/badge/Vite-Build-646cff?style=for-the-badge&logo=vite)

**DouyinMediaHub** is a sophisticated, mobile-first media management dashboard designed to bridge the gap between social media consumption and personal content archiving. It allows users to parse, analyze, download, and organize content from Douyin (TikTok China) in a "Cinematic Dark" interface.

> **Note:** This is a modern React frontend application. It is designed to work with a Python backend (for actual scraping) and Supabase (for data persistence), but includes a robust **Mock Mode** for instant demonstration without backend dependencies.

---

## ✨ Key Features

### 🔗 Smart Link Parser
*   **Universal Input:** Accepts raw shared links (e.g., `v.douyin.com/...`) or direct IDs.
*   **Real-time Analysis:** Simulates WebSocket connections to provide live progress logs during the parsing and downloading process.
*   **Metadata Extraction:** Automatically retrieves video resolution, bitrate, author details, hashtags, and engagement metrics.

### 📚 Immersive Media Library
*   **Adaptive Grid (Waterfall):** A responsive masonry layout that optimizes screen real estate.
    *   **Mobile:** 2-column high-density layout.
    *   **Desktop:** 3-5 column expansive layout.
*   **Smart Previews:**
    *   **Hover-to-Play:** Desktop users can hover over cards to preview video content with sound.
    *   **Carousel Support:** Native support for multi-image posts (Image Atlases) with slide controls.
*   **Multiple View Modes:**
    *   **Grid:** Visual-first browsing.
    *   **Table:** Data-dense list for bulk management and sorting.
    *   **Feed:** Vertical, full-screen "TikTok-style" playback with auto-play on scroll.

### 📊 Analytics Dashboard
*   **Visual Insights:** Interactive charts powered by `recharts`.
*   **Metrics:** Track download frequency, storage usage trends, and media type distribution (Video vs. Image vs. Audio).
*   **System Health:** Monitor API key usage and parsing success rates.

### ⚙️ Enterprise-Grade Management
*   **API Key System:** Create, manage, and scope API keys with expiration dates for third-party access.
*   **User Profiles:** Manage avatars, security settings, and display preferences.
*   **Dark Mode UI:** A refined "Zinc" color palette designed for low-light environments and high-contrast media viewing.

---

## 🛠 Tech Stack & Architecture

### Frontend Core
*   **Framework:** React 19 (Leveraging latest Hooks & concurrent features).
*   **Build Tool:** Vite (Lightning fast HMR).
*   **Styling:** Tailwind CSS + Custom Scrollbars + Glassmorphism effects.
*   **Icons:** Lucide React.

### Data & State
*   **Type Safety:** Fully typed with TypeScript interfaces (`DouyinBase`, `UserProfile`).
*   **Persistence:** Supabase Client (JS) for database operations.
*   **Mocking:** robust `services/parserService.ts` for offline development.

---

## 🚀 Getting Started

### Prerequisites
*   Node.js (v18 or higher)
*   npm or yarn

### Installation

1.  **Clone the repository**
    ```bash
    git clone https://github.com/yourusername/douyin-media-hub.git
    cd douyin-media-hub
    ```

2.  **Install Dependencies**
    ```bash
    npm install
    ```

3.  **Start Development Server**
    ```bash
    npm run dev
    # or
    npm start
    ```

4.  **Open in Browser**
    Visit `http://localhost:5173` (or the port shown in your terminal).

---

## 🗄️ Database Setup (Supabase)

To enable cloud persistence, you need to create a table in your Supabase project.

1.  Go to the **SQL Editor** in your Supabase Dashboard.
2.  Run the following SQL query to create the `douyin_items` table:

```sql
-- Create the main items table
create table public.douyin_items (
  aweme_id text not null primary key,
  video_title text,
  author text,
  video_original_url text,
  video_download_urls text[], -- Array of strings
  image_download_urls text[], -- Array of strings
  music_download_urls text[], -- Array of strings
  music_name text,
  video_digg_count numeric,
  video_comment_count numeric,
  video_share_count numeric,
  video_collect_count numeric,
  video_created_time timestamptz,
  aweme_type text,
  video_desc text,
  video_duration text,
  video_resolution text,
  tags text[],
  notes text,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

-- Enable Row Level Security (RLS) if you want to restrict access
alter table public.douyin_items enable row level security;

-- Create a policy that allows anyone to read/write (FOR DEMO ONLY - Secure this in production!)
create policy "Allow public access" on public.douyin_items
for all using (true) with check (true);
```

3.  **Configure Environment Variables:**
    Create a `.env` file in the root directory (or configure via the Settings UI in the app):

```env
VITE_SUPABASE_URL=https://your-project-id.supabase.co
VITE_SUPABASE_ANON_KEY=your-anon-key-here
```

---

## 📂 Project Structure

```
src/
├── components/          # UI Components
│   ├── CompactMediaCard.tsx   # Grid item (Waterfall style)
│   ├── MediaCard.tsx          # Detail view item
│   ├── LibraryFeed.tsx        # Vertical feed player
│   ├── LibraryTable.tsx       # Data table view
│   ├── SettingsView.tsx       # Config & API Keys
│   └── StatsChart.tsx         # Analytics charts
├── services/            # Business Logic
│   ├── dataService.ts         # Supabase CRUD wrappers
│   └── parserService.ts       # Mock backend logic
├── types.ts             # TypeScript Interfaces
├── constants.ts         # Mock Data
├── supabaseClient.ts    # DB Connection Singleton
└── App.tsx              # Main Layout & Router
```

---

## 📖 User Guide

### 1. Parsing a Video
1.  Navigate to the **Link Parser** tab.
2.  Paste a Douyin link (e.g., `https://v.douyin.com/k9we...`).
3.  Click **Analyze**. The system will simulate a connection to the backend, showing logs for "Connecting", "Downloading", and "Processing".
4.  Once complete, the result is displayed. Click **Save to Library** to store it.

### 2. Managing Library
1.  Go to **My Library**.
2.  **Grid View:** Use for browsing. Hover over videos to play them (desktop).
3.  **List View:** Use for sorting by Likes, Comments, or Date. Click "Edit" to add Tags or Notes.
4.  **Search:** Use the top search bar to filter by Title, Author, or Tags.

### 3. API Keys
1.  Go to **Settings > API Management**.
2.  Click **Create API Key**.
3.  Select an expiration date and required scopes (e.g., `/api/v1/douyin/web/`).
4.  Copy the generated key immediately (it won't be shown again).

---

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1.  Fork the project
2.  Create your feature branch (`git checkout -b feature/AmazingFeature`)
3.  Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4.  Push to the branch (`git push origin feature/AmazingFeature`)
5.  Open a Pull Request

---

## 📄 License

Distributed under the MIT License. See `LICENSE` for more information.
