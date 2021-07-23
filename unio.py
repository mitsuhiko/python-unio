import io
import os
import sys
import codecs
import contextlib


# We do not trust traditional unixes about having reliable file systems.
# In that case we know better than what the env says and declare this to
# be utf-8 always.
has_likely_buggy_unicode_filesystem = \
    sys.platform.startswith('linux') or 'bsd' in sys.platform


def is_ascii_encoding(encoding):
    """Given an encoding this figures out if the encoding is actually ASCII
    (which is something we don't actually want in most cases).  This is
    necessary because ASCII comes under many names such as ANSI_X3.4-1968.
    """
    if encoding is None:
        return False
    try:
        codec = codecs.lookup(encoding)
    except LookupError:
        return False
    return codec.name == 'ascii'


def get_filesystem_encoding():
    """Returns the filesystem encoding that should be used.  Note that
    this is different from the Python understanding of the filesystem
    encoding which might be deeply flawed.  Do not use this value against
    Python's unicode APIs because it might be different.

    The concept of a filesystem encoding in generally is not something
    you should rely on.  As such if you ever need to use this function
    except for writing wrapper code reconsider.
    """
    if has_likely_buggy_unicode_filesystem:
        return 'utf-8'
    rv = sys.getfilesystemencoding()
    if is_ascii_encoding(rv):
        return 'utf-8'
    return rv


def get_file_encoding(for_writing=False):
    """Returns the encoding for text file data.  This is always the same
    on all operating systems because this is the only thing that makes
    sense when wanting to make data exchange feasible.  This is utf-8 no
    questions asked.  The only simplification is that if a file is opened
    for reading then we allo utf-8-sig.
    """
    if for_writing:
        return 'utf-8'
    return 'utf-8-sig'


def get_std_stream_encoding():
    """Returns the default stream encoding if not found."""
    rv = sys.getdefaultencoding()
    if is_ascii_encoding(rv):
        return 'utf-8'
    return rv


class BrokenEnvironment(Exception):
    """This error is raised on Python 3 if the system was misconfigured
    beyond repair.
    """


class _NonClosingTextIOWrapper(io.TextIOWrapper):
    """Subclass of the wrapper that does not close the underlying file
    in the destructor.  This is necessary so that our wrapping of the
    standard streams does not accidentally close the original file.
    """

    def __del__(self):
        pass


class _FixupStream(object):
    """The new io interface needs more from streams than streams
    traditionally implement.  As such this fixup stuff is necessary in
    some circumstances.
    """

    def __init__(self, stream):
        self._stream = stream

    def __getattr__(self, name):
        return getattr(self._stream, name)

    def readable(self):
        x = getattr(self._stream, 'readable', None)
        if x is not None:
            return x
        try:
            self._stream.read(0)
        except Exception:
            return False
        return True

    def writable(self):
        x = getattr(self._stream, 'writable', None)
        if x is not None:
            return x
        try:
            self._stream.write('')
        except Exception:
            try:
                self._stream.write(b'')
            except Exception:
                return False
        return True

    def seekable(self):
        x = getattr(self._stream, 'seekable', None)
        if x is not None:
            return x
        try:
            self._stream.seek(self._stream.tell())
        except Exception:
            return False
        return True


PY2 = sys.version_info[0] == 2
if PY2:
    import StringIO
    text_type = unicode

    TextIO = io.StringIO
    BytesIO = io.BytesIO
    NativeIO = StringIO.StringIO

    def _make_text_stream(stream, encoding, errors):
        if encoding is None:
            encoding = get_std_stream_encoding()
        if errors is None:
            errors = 'replace'
        return _NonClosingTextIOWrapper(_FixupStream(stream), encoding, errors)

    def get_binary_stdin():
        return sys.stdin

    def get_binary_stdout():
        return sys.stdout

    def get_binary_stderr():
        return sys.stderr

    def get_binary_argv():
        return list(sys.argv)

    def get_text_stdin(encoding=None, errors=None):
        return _make_text_stream(sys.stdin, encoding, errors)

    def get_text_stdout(encoding=None, errors=None):
        return _make_text_stream(sys.stdout, encoding, errors)

    def get_text_stderr(encoding=None, errors=None):
        return _make_text_stream(sys.stderr, encoding, errors)

    @contextlib.contextmanager
    def wrap_standard_stream(stream_type, stream):
        if stream_type not in ('stdin', 'stdout', 'stderr'):
            raise TypeError('Invalid stream %s' % stream_type)
        old_stream = getattr(sys, stream_type)
        setattr(sys, stream_type, stream)
        try:
            yield stream
        finally:
            setattr(sys, stream_type, old_stream)

    @contextlib.contextmanager
    def capture_stdout(and_stderr=False):
        stream = StringIO.StringIO()
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        sys.stdout = stream
        if and_stderr:
            sys.stderr = stream
        try:
            yield stream
        finally:
            sys.stdout = old_stdout
            if and_stderr:
                sys.stderr = old_stderr

    binary_env = os.environ
else:
    text_type = str

    TextIO = io.StringIO
    BytesIO = io.BytesIO
    NativeIO = io.StringIO

    def _is_binary_reader(stream, default=False):
        try:
            return isinstance(stream.read(0), bytes)
        except Exception:
            return default
            # This happens in some cases where the stream was already
            # closed.  In this case we assume the default.

    def _is_binary_writer(stream, default=False):
        try:
            stream.write(b'')
        except Exception:
            try:
                stream.write('')
                return False
            except Exception:
                pass
            return default
        return True

    def _find_binary_reader(stream):
        # We need to figure out if the given stream is already binary.
        # This can happen because the official docs recommend detaching
        # the streams to get binary streams.  Some code might do this, so
        # we need to deal with this case explicitly.
        is_binary = _is_binary_reader(stream, False)

        if is_binary:
            return stream

        buf = getattr(stream, 'buffer', None)
        # Same situation here, this time we assume that the buffer is
        # actually binary in case it's closed.
        if buf is not None and _is_binary_reader(buf, True):
            return buf

    def _find_binary_writer(stream):
        # We need to figure out if the given stream is already binary.
        # This can happen because the official docs recommend detaching
        # the streams to get binary streams.  Some code might do this, so
        # we need to deal with this case explicitly.
        if _is_binary_writer(stream, False):
            return stream

        buf = getattr(stream, 'buffer', None)

        # Same situation here, this time we assume that the buffer is
        # actually binary in case it's closed.
        if buf is not None and _is_binary_reader(buf, True):
            return buf

    def _stream_is_misconfigured(stream):
        """A stream is misconfigured if it's encoding is ASCII."""
        return is_ascii_encoding(getattr(stream, 'encoding', None))

    def _wrap_stream_for_text(stream, encoding, errors):
        if errors is None:
            errors = 'replace'
        if encoding is None:
            encoding = get_std_stream_encoding()
        return _NonClosingTextIOWrapper(_FixupStream(stream), encoding, errors)

    def _is_compatible_text_stream(stream, encoding, errors):
        stream_encoding = getattr(stream, 'encoding', None)
        stream_errors = getattr(stream, 'errors', None)

        # Perfect match.
        if stream_encoding == encoding and stream_errors == errors:
            return True

        # Otherwise it's only a compatible stream if we did not ask for
        # an encoding.
        if encoding is None:
            return stream_encoding is not None

        return False

    def _force_correct_text_reader(text_reader, encoding, errors):
        if _is_binary_reader(text_reader, False):
            binary_reader = text_reader
        else:
            # If there is no target encoding set we need to verify that the
            # reader is actually not misconfigured.
            if encoding is None and not _stream_is_misconfigured(text_reader):
                return text_reader

            if _is_compatible_text_stream(text_reader, encoding, errors):
                return text_reader

            # If the reader has no encoding we try to find the underlying
            # binary reader for it.  If that fails because the environment is
            # misconfigured, we silently go with the same reader because this
            # is too common to happen.  In that case mojibake is better than
            # exceptions.
            binary_reader = _find_binary_reader(text_reader)
            if binary_reader is None:
                return text_reader

        # At this point we default the errors to replace instead of strict
        # because nobody handles those errors anyways and at this point
        # we're so fundamentally fucked that nothing can repair it.
        if errors is None:
            errors = 'replace'
        return _wrap_stream_for_text(binary_reader, encoding, errors)

    def _force_correct_text_writer(text_writer, encoding, errors):
        if _is_binary_writer(text_writer, False):
            binary_writer = text_writer
        else:
            # If there is no target encoding set we need to verify that the
            # writer is actually not misconfigured.
            if encoding is None and not _stream_is_misconfigured(text_writer):
                return text_writer

            if _is_compatible_text_stream(text_writer, encoding, errors):
                return text_writer

            # If the writer has no encoding we try to find the underlying
            # binary writer for it.  If that fails because the environment is
            # misconfigured, we silently go with the same writer because this
            # is too common to happen.  In that case mojibake is better than
            # exceptions.
            binary_writer = _find_binary_writer(text_writer)
            if binary_writer is None:
                return text_writer

        # At this point we default the errors to replace instead of strict
        # because nobody handles those errors anyways and at this point
        # we're so fundamentally fucked that nothing can repair it.
        if errors is None:
            errors = 'replace'
        return _wrap_stream_for_text(binary_writer, encoding, errors)

    def get_binary_stdin():
        reader = _find_binary_reader(sys.stdin)
        if reader is None:
            raise BrokenEnvironment('Was not able to determine binary '
                                    'stream for sys.stdin.')
        return reader

    def get_binary_stdout():
        writer = _find_binary_writer(sys.stdout)
        if writer is None:
            raise BrokenEnvironment('Was not able to determine binary '
                                    'stream for sys.stdout.')
        return writer

    def get_binary_stderr():
        writer = _find_binary_writer(sys.stderr)
        if writer is None:
            raise BrokenEnvironment('Was not able to determine binary '
                                    'stream for sys.stderr.')
        return writer

    def get_text_stdin(encoding=None, errors=None):
        return _force_correct_text_reader(sys.stdin, encoding, errors)

    def get_text_stdout(encoding=None, errors=None):
        return _force_correct_text_writer(sys.stdout, encoding, errors)

    def get_text_stderr(encoding=None, errors=None):
        return _force_correct_text_writer(sys.stderr, encoding, errors)

    def get_binary_argv():
        fs_enc = sys.getfilesystemencoding()
        return [x.encode(fs_enc, 'surrogateescape') for x in sys.argv]

    binary_env = os.environb

    @contextlib.contextmanager
    def wrap_standard_stream(stream_type, stream):
        old_stream = getattr(sys, stream_type, None)
        if stream_type == 'stdin':
            if _is_binary_reader(stream):
                raise TypeError('Standard input stream cannot be set to a '
                                'binary reader directly.')
            if _find_binary_reader(stream) is None:
                raise TypeError('Standard input stream needs to be backed '
                                'by a binary stream.')
        elif stream_type in ('stdout', 'stderr'):
            if _is_binary_writer(stream):
                raise TypeError('Standard output stream cannot be set to a '
                                'binary writer directly.')
            if _find_binary_writer(stream) is None:
                raise TypeError('Standard output and error streams need '
                                'to be backed by a binary streams.')
        else:
            raise TypeError('Invalid stream %s' % stream_type)
        setattr(sys, stream_type, stream)
        try:
            yield old_stream
        finally:
            setattr(sys, stream_type, old_stream)

    class _CapturedStream(object):
        """A helper that flushes before getvalue() to fix a few oddities
        on Python 3.
        """

        def __init__(self, stream):
            self._stream = stream

        def __getattr__(self, name):
            return getattr(self._stream, name)

        def getvalue(self):
            self._stream.flush()
            return self._stream.buffer.getvalue()

        def __repr__(self):
            return repr(self._stream)

    @contextlib.contextmanager
    def capture_stdout(and_stderr=False):
        """Captures stdout and yields the new bytes stream that backs it.
        It also wraps it in a fake object that flushes on getting the
        underlying value.
        """
        ll_stream = io.BytesIO()
        stream = _NonClosingTextIOWrapper(ll_stream, sys.stdout.encoding,
                                          sys.stdout.errors)
        old_stdout = sys.stdout
        sys.stdout = stream

        if and_stderr:
            old_stderr = sys.stderr
            sys.stderr = stream

        try:
            yield _CapturedStream(stream)
        finally:
            stream.flush()
            sys.stdout = old_stdout
            if and_stderr:
                sys.stderr = old_stderr


def _fixup_path(path):
    if has_likely_buggy_unicode_filesystem \
       and isinstance(path, text_type):
        if PY2:
            path = path.encode(get_filesystem_encoding())
        else:
            path = path.encode(get_filesystem_encoding(),
                               'surrogateescape')
    return path


def open(filename, mode='r', encoding=None, errors=None):
    """Opens a file either in text or binary mode.  The encoding for the
    file is automatically detected.
    """
    filename = _fixup_path(filename)
    if 'b' not in mode:
        encoding = get_file_encoding('w' in mode)
    if encoding is not None:
        return io.open(filename, mode, encoding=encoding, errors=errors)
    return io.open(filename, mode)
