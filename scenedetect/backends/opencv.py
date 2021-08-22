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
""" ``scenedetect.backends.opencv`` Module

This module contains the :py:class:`VideoStreamCv2` class, which provides an OpenCV
based video decoder (based on cv2.VideoCapture).
"""

import math
from typing import Tuple, Union, Optional
import os.path

import cv2
from numpy import ndarray

from scenedetect.frame_timecode import FrameTimecode, MINIMUM_FRAMES_PER_SECOND_FLOAT
from scenedetect.platform import get_aspect_ratio, logger
from scenedetect.video_stream import VideoStream, SeekError, VideoOpenFailure


class VideoStreamCv2(VideoStream):
    """ OpenCV VideoCapture backend. """

    def __init__(self, path_or_device: Union[str, int], override_framerate: Optional[float] = None):
        """Open a new OpenCV backend."""
        super().__init__()

        self._path_or_device = path_or_device
        self._is_device = isinstance(self._path_or_device, int)

        # Initialized in _open_capture:
        self._cap = None    # Reference to underlying cv2.VideoCapture object.
        self._frame_rate = None

        # VideoCapture state
        self._has_seeked = False
        self._has_grabbed = False

        self._open_capture(override_framerate)

    @property
    def capture(self) -> cv2.VideoCapture:
        """Returns reference to underlying VideoCapture object.

        Do not seek nor call the read/grab methods through the VideoCapture otherwise the
        VideoStreamCv2 object will be in an inconsistent state."""
        return self._cap

    #
    # VideoStream Methods/Properties
    #

    @property
    def frame_rate(self) -> float:
        """Framerate in frames/sec."""
        return self._frame_rate

    @property
    def path(self) -> str:
        """Video or device path."""
        if self._is_device:
            return "Device %d" % self._path_or_device
        return self._path_or_device

    @property
    def is_seekable(self) -> bool:
        """True if seek() is allowed, False otherwise.

        Always False if opening a device/webcam."""
        return not self._is_device

    @property
    def frame_size(self) -> Tuple[int, int]:
        """Size of each video frame in pixels as a tuple of (width, height)."""
        return (math.trunc(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                math.trunc(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))

    @property
    def duration(self) -> Optional[FrameTimecode]:
        """Duration of the stream as a FrameTimecode, or None if non terminating."""
        if self._is_device:
            return None
        return self.base_timecode + math.trunc(self._cap.get(cv2.CAP_PROP_FRAME_COUNT))

    @property
    def aspect_ratio(self) -> float:
        """Display/pixel aspect ratio as a float (1.0 represents square pixels)."""
        return get_aspect_ratio(self._cap)

    @property
    def position(self) -> FrameTimecode:
        """Current position within stream as FrameTimecode.

        This can be interpreted as presentation time stamp, thus if `frame_number` is 1,
        the timestamp is equivalent to presentation time 0.

        This method will always return 0 (e.g. be equal to `base_timecode`) if no frames
        have been `read`."""
        if self.frame_number < 1:
            return self.base_timecode
        return self.base_timecode + (self.frame_number - 1)

    @property
    def position_ms(self) -> float:
        """Current position within stream as a float of the presentation time in milliseconds.
        The first frame has a time of 0.0 ms.

        Cannot be used after calling `seek` until calling `read` with `advance=True`.
        Use `position` or `frame_number` instead.

        This method will always return 0.0 if no frames have been `read`."""
        if self._has_seeked:
            raise RuntimeError(
                "Cannot access `position_ms` in a VideoStreamCv2 after calling `seek` until"
                " calling `read` with `advance=True`.")
        return self._cap.get(cv2.CAP_PROP_POS_MSEC)

    @property
    def frame_number(self) -> int:
        """Current position within stream in frames as an int.

        1 indicates the first frame, whereas 0 indicates that no frames have been `read`.

        This method will always return 0 if no frames have been `read`."""
        return math.trunc(self._cap.get(cv2.CAP_PROP_POS_FRAMES))

    def seek(self, target: Union[FrameTimecode, float, int]):
        """Seek to the given timecode. Retrieve frame with `read(advance=False)`.

        Seeking past the end of video shall be equivalent to seeking to the last frame.

        Not supported if the VideoStream is a device/camera.  Untested with web streams.

        Arguments:
            target: Target position in video stream to seek to. Interpreted based on type.
              If FrameTimecode, backend can seek using any representation (preferably native when
              VFR support is added).
              If float, interpreted as time in seconds.
              If int, interpreted as frame number, starting from 1.
        Raises:
            SeekError if an unrecoverable error occurs while seeking, or seeking is not
            supported (either by the backend entirely, or if the input is a stream).
        """
        if self._is_device:
            raise SeekError("Cannot seek if input is a device!")
        if isinstance(target, int) and not target > 0:
            raise ValueError("Target frame number must start from 1!")
        if isinstance(target, float) and target < 0.0:
            raise ValueError("Target time in seconds must be positive!")

        # Handle case of int 0 separately due to OpenCV/ffmpeg time handling.
        # Specifically, in a VideoCapture object, seeking to frame 1 yields a pts of 0ms,
        # but seeking to 0ms yields frame 0 instead of frame 1).
        if isinstance(target, int) and target == 0:
            self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0.0)
            # Seeking to position 0 will not decode any frames.
            self._has_grabbed = False
            return
        # At this point we can just use a FrameTimecode to convert the target into frames.
        # However, since FrameTimecode 0 maps to frame 1 for a VideoStream, we must handle
        # that transformation here.
        if isinstance(target, int):
            target -= 1
        # Correct from our internal 0-based representation to external 1-based.
        target_frame_cv2 = (self.base_timecode + target).get_frames()
        # Have to seek one behind and call grab() after to that the VideoCapture
        # returns a valid timestamp when using CAP_PROP_POS_MSEC.
        self._cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame_cv2)
        self._cap.grab()
        self._has_grabbed = True

    def reset(self):
        """ Close and re-open the VideoStream (should be equivalent to calling `seek(0)`). """
        self._cap.release()
        self._open_capture(self._frame_rate)

    def read(self, decode: bool = True, advance: bool = True) -> Optional[ndarray]:
        """ Return next frame (or current if advance = False), or None if end of video.

        If decode = False, None will be returned, but will be slightly faster.

        If decode and advance are both False, equivalent to a no-op.

        It is undefined what happens if you call `read` with `advance=False` after calling `seek`.
        """
        if not self._cap.isOpened():
            return None
        if advance:
            self._cap.grab()
            self._has_grabbed = True
            self._has_seeked = False
        if decode and self._has_grabbed:
            _, frame = self._cap.retrieve()
            return frame
        return None

    #
    # Private Methods
    #

    def _open_capture(self, framerate: Optional[float] = None):
        """Opens capture referenced by this object and resets internal state."""
        if self._is_device and self._path_or_device < 0:
            raise ValueError("Invalid/negative device ID specified.")
        # Check if files exist if passed video file is not an image sequence
        # (checked with presence of % in filename) or not a URL (://).
        if not self._is_device and not ('%' in self._path_or_device
                                        or '://' in self._path_or_device):
            if not os.path.exists(self._path_or_device):
                raise IOError("Video file not found.")

        cap = cv2.VideoCapture(self._path_or_device)
        if not cap.isOpened():
            raise VideoOpenFailure("isOpened() returned False when opening OpenCV VideoCapture!")

        # Display a warning if the video codec type seems unsupported (#86).
        if int(abs(cap.get(cv2.CAP_PROP_FOURCC))) == 0:
            logger.error(
                "Video codec detection failed, output may be incorrect.\nThis could be caused"
                " by using an outdated version of OpenCV, or using codecs that currently are"
                " not well supported (e.g. VP9).\n"
                "As a workaround, consider re-encoding the source material before processing.\n"
                "For details, see https://github.com/Breakthrough/PySceneDetect/issues/86")

        # Ensure the framerate is correct to avoid potential divide by zero errors. This can be
        # addressed in the PyAV backend if required since it supports integer timebases.
        if not framerate:
            framerate = cap.get(cv2.CAP_PROP_FPS)
            if framerate < MINIMUM_FRAMES_PER_SECOND_FLOAT:
                raise VideoOpenFailure(
                    "Unable to obtain video framerate! Check the file/device/stream, or set the"
                    " `framerate` to assume a given framerate.")

        self._cap = cap
        self._frame_rate = framerate
        self._has_seeked = False
        self._has_grabbed = False