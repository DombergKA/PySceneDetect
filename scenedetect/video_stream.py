# -*- coding: utf-8 -*-
#
#         PySceneDetect: Python-Based Video Scene Detector
#   ---------------------------------------------------------------
#     [  Site: http://www.bcastell.com/projects/PySceneDetect/   ]
#     [  Github: https://github.com/Breakthrough/PySceneDetect/  ]
#     [  Documentation: http://pyscenedetect.readthedocs.org/    ]
#
# Copyright (C) 2014-2021 Brandon Castellano <http://www.bcastell.com>.
#
# PySceneDetect is licensed under the BSD 3-Clause License; see the included
# LICENSE file, or visit one of the following pages for details:
#  - https://github.com/Breakthrough/PySceneDetect/
#  - http://www.bcastell.com/projects/PySceneDetect/
#
# This software uses Numpy, OpenCV, click, tqdm, simpletable, and pytest.
# See the included LICENSE files or one of the above URLs for more information.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.  IN NO EVENT SHALL THE
# AUTHORS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN
# ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
# WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
#
""" ``scenedetect.video_stream`` Module

This module contains the :py:class:`VideoStream` class, which provides a consistent
interface to reading videos which is library agnostic.  This allows PySceneDetect to
support multiple video backends.

"""

from abc import ABC, abstractmethod
from typing import Tuple, Optional, Union

from numpy import ndarray

from scenedetect.platform import logger
from scenedetect.frame_timecode import FrameTimecode

##
## VideoManager Exceptions
##


class SeekError(Exception):
    """Either an unrecoverable error happened while attempting to seek, or the underlying
    stream is not seekable (additional information will be provided when possible)."""


class VideoOpenFailure(Exception):
    """May be raised by a backend if opening a video fails."""


##
## VideoStream Constants & Helper Functions
##

DEFAULT_DOWNSCALE_FACTORS = {
    3200: 12,    # ~4k
    2100: 8,    # ~2k
    1700: 6,    # ~1080p
    1200: 5,
    900: 4,    # ~720p
    600: 3,
    400: 2    # ~480p
}
"""Dict[int, int]: The default downscale factor for a video of size W x H,
which enforces the constraint that W >= 200 to ensure an adequate amount
of pixels for scene detection while providing a speedup in processing. """


def compute_downscale_factor(frame_width: int) -> int:
    """ Compute Downscale Factor: Returns the optimal default downscale factor based on
    a video's resolution (specifically, the width parameter).

    Returns:
        int: The defalt downscale factor to use with a video of frame_height x frame_width.
    """
    for width in sorted(DEFAULT_DOWNSCALE_FACTORS, reverse=True):
        if frame_width >= width:
            return DEFAULT_DOWNSCALE_FACTORS[width]
    return 1


class VideoStream(ABC):
    """ Interface which all video backends must implement. """

    def __init__(self):
        self._downscale: int = 1

    #
    # TODO: Move responsibility for downscaling into SceneManager to help simplify
    # this interface.
    #
    @property
    def downscale(self) -> int:
        """Factor to downscale each frame by. Will always be >= 1, where 1
        indicates no scaling."""
        return self._downscale

    @downscale.setter
    def downscale(self, downscale_factor: int):
        """Set to 1 for no downscaling, 2 for 2x downscaling, 3 for 3x, etc..."""
        if downscale_factor < 1:
            raise ValueError("Downscale factor must be a positive integer >= 1!")
        if downscale_factor is not None and not isinstance(downscale_factor, int):
            logger.warning("Downscale factor will be truncated to integer!")
            downscale_factor = int(downscale_factor)
        self._downscale = downscale_factor

    def downscale_auto(self):
        """Sets downscale factor automatically based on video frame width."""
        self.downscale = compute_downscale_factor(self.frame_size[0])

    @property
    def frame_size_effective(self) -> Tuple[int, int]:
        """Get effective framesize taking into account downscale if set."""
        if self.downscale is None:
            return self.frame_size
        return (self.frame_size[0] / self.downscale, self.frame_size[1] / self.downscale)


    @property
    def base_timecode(self) -> FrameTimecode:
        """Base FrameTimecode object to use as a time base."""
        return FrameTimecode(timecode=0, fps=self.frame_rate)

    #
    # Abstract Properties
    #

    @property
    @abstractmethod
    def path(self) -> str:
        """Video or device path."""
        raise NotImplementedError


    @property
    @abstractmethod
    def is_seekable(self) -> bool:
        """True if seek() is allowed, False otherwise."""
        raise NotImplementedError

    @property
    @abstractmethod
    def frame_rate(self) -> float:
        """Frame rate in frames/sec."""
        raise NotImplementedError

    @property
    @abstractmethod
    def duration(self) -> Optional[FrameTimecode]:
        """Duration of the stream as a FrameTimecode, or None if non terminating."""
        raise NotImplementedError

    @property
    @abstractmethod
    def frame_size(self) -> Tuple[int, int]:
        """Size of each video frame in pixels as a tuple of (width, height)."""
        raise NotImplementedError

    @property
    @abstractmethod
    def aspect_ratio(self) -> float:
        """Display/pixel aspect ratio as a float (1.0 represents square pixels)."""
        raise NotImplementedError

    @property
    @abstractmethod
    def position(self) -> FrameTimecode:
        """Current position within stream as FrameTimecode.

        This can be interpreted as presentation time stamp, thus frame 1 corresponds
        to the presentation time 0.  Returns 0 even if `frame_number` is 0."""
        raise NotImplementedError

    @property
    @abstractmethod
    def position_ms(self) -> float:
        """Current position within stream as a float of the presentation time in
        milliseconds. The first frame has a PTS of 0."""
        raise NotImplementedError

    @property
    @abstractmethod
    def frame_number(self) -> int:
        """Current position within stream as the frame number.

        Will return 0 until the first frame is `read`."""
        raise NotImplementedError


    #
    # Abstract Methods
    #

    @abstractmethod
    def read(self, decode: bool = True, advance: bool = True) -> Optional[ndarray]:
        """ Returns next frame (or current if advance = False), or None if end of video.

        If decode = False, None will be returned, but will be slightly faster.

        If decode and advance are both False, equivalent to a no-op.
        """
        raise NotImplementedError

    @abstractmethod
    def reset(self):
        """ Close and re-open the VideoStream (equivalent to seeking back to beginning). """
        raise NotImplementedError


    @abstractmethod
    def seek(self, target: Union[FrameTimecode, float, int]):
        """Seeks to the given timecode. The values of all properties/methods are undefined after
        calling `seek` until `read` is called  with `advance=True` (the default).

        Frame 0 has a (presentation) timecode of 0.

        May not be supported on all backends/types of videos (e.g. cameras).

        Internally, PySceneDetect maps the first frame to 0 to simplify timecode handling.
        Note that most external libraries denote the first frame as 1, so correction for this is
        sometimes required.

        Arguments:
            target: Target position in video stream to seek to. Interpreted based on type.
              If FrameTimecode, backend can seek using any representation (preferably native when
              VFR support is added).
              If float, interpreted as time in seconds.
              If int, interpreted as frame number.
        Raises:
            SeekError: An unrecoverable error occurs while seeking, or seeking is not
              supported (either by the backend entirely, or if the input is a stream).
            ValueError: `target` is not a valid value (i.e. it is negative).
        """
        raise NotImplementedError
