from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ImageGenResult:
    image_url: str
    image_path: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    provider: str = ""
    model: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class GenResult:
    """A produced media file on the local filesystem.

    The shape every file-producing provider hands back (jimeng CLI, the
    nous-engine bridge). Lives here rather than in one provider so a caller
    like the upscale route never has to import a specific vendor's module to
    name the type it receives.
    """

    local_path: str
    mime: str
    raw: dict = field(default_factory=dict)


@dataclass
class VideoGenResult:
    video_url: str
    video_path: Optional[str] = None
    duration_seconds: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None
    thumbnail_url: Optional[str] = None
    provider: str = ""
    model: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class TaskStatus:
    task_id: str
    status: str  # 'pending', 'processing', 'completed', 'failed'
    progress: float = 0.0  # 0-100
    result: Optional[ImageGenResult | VideoGenResult] = None
    error: Optional[str] = None


class BaseImageProvider(ABC):
    @abstractmethod
    async def generate(self, prompt: str, model: str, **kwargs) -> ImageGenResult: ...

    @abstractmethod
    async def check_status(self, task_id: str) -> TaskStatus: ...

    @abstractmethod
    def list_models(self) -> list[str]: ...


class BaseVideoProvider(ABC):
    @abstractmethod
    async def generate(
        self, source_image_url: str, prompt: str, model: str, **kwargs
    ) -> VideoGenResult: ...

    @abstractmethod
    async def check_status(self, task_id: str) -> TaskStatus: ...

    @abstractmethod
    def list_models(self) -> list[str]: ...
