"""Backend adapter built solely on :mod:`ctypes` and Windows Core Audio."""
from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass
from typing import Any

from .model import Endpoint
from .wasapi import (
    AUDCLNT_BUFFERFLAGS_SILENT, AUDCLNT_SHAREMODE_SHARED,
    AUDCLNT_STREAMFLAGS_EVENTCALLBACK, AUDCLNT_STREAMFLAGS_LOOPBACK,
    CLSCTX_ALL, CLSID_MMDeviceEnumerator, DEVICE_STATE_ACTIVE, DWORD,
    HRESULT, IID_IAudioCaptureClient, IID_IAudioClient, IID_IMMDeviceEnumerator,
    LPVOID, PKEY_Device_FriendlyName, PROPVARIANT, STGM_READ, UINT32,
    VT_LPWSTR, WAIT_OBJECT_0, WAIT_TIMEOUT, WAVEFORMATEX, AudioFormat,
    ComApartment, WasapiUnavailable, _method, _require_windows, check_hresult,
    WAVE_FORMAT_PCM, eCapture, eConsole, eMultimedia, eCommunications, eRender,
    interpret_format, pcm16, release,
)


@dataclass(frozen=True)
class NativeEndpointInfo:
    endpoint: Endpoint
    flow: int


def _pcm16_engine_format(source: AudioFormat) -> WAVEFORMATEX:
    """Build the same-rate/channel PCM16 format requested from the audio engine."""
    block_align = source.channels * 2
    return WAVEFORMATEX(WAVE_FORMAT_PCM, source.channels, source.sample_rate,
                        source.sample_rate * block_align, block_align, 16, 0)


def _create_enumerator() -> LPVOID:
    ole32, _ = _require_windows()
    result = LPVOID()
    check_hresult(ole32.CoCreateInstance(
        ctypes.byref(CLSID_MMDeviceEnumerator), None, CLSCTX_ALL,
        ctypes.byref(IID_IMMDeviceEnumerator), ctypes.byref(result)), "CoCreateInstance(MMDeviceEnumerator)")
    return result


def _device_id(device: LPVOID) -> str:
    value = ctypes.c_wchar_p()
    check_hresult(_method(device, 5, HRESULT, ctypes.POINTER(ctypes.c_wchar_p))(device, ctypes.byref(value)), "IMMDevice.GetId")
    try:
        return value.value or ""
    finally:
        ctypes.windll.ole32.CoTaskMemFree(value)


def _default_device_id(enumerator: LPVOID, flow: int, role: int = eConsole) -> str:
    device = LPVOID()
    check_hresult(_method(enumerator, 4, HRESULT, ctypes.c_int, ctypes.c_int, ctypes.POINTER(LPVOID))(
        enumerator, flow, role, ctypes.byref(device)), "IMMDeviceEnumerator.GetDefaultAudioEndpoint")
    try:
        return _device_id(device)
    finally:
        release(device)


def _safe_default_device_id(enumerator: LPVOID, flow: int, role: int = eConsole) -> str | None:
    try:
        if role == eConsole:
            return _default_device_id(enumerator, flow)
        return _default_device_id(enumerator, flow, role)
    except OSError as exc:
        print(f"Could not determine default endpoint: {exc}", file=sys.stderr)
        return None


def _friendly_name(device: LPVOID) -> str:
    store = LPVOID()
    prop = PROPVARIANT()
    check_hresult(_method(device, 4, HRESULT, DWORD, ctypes.POINTER(LPVOID))(device, STGM_READ, ctypes.byref(store)), "IMMDevice.OpenPropertyStore")
    try:
        check_hresult(_method(store, 5, HRESULT, ctypes.POINTER(type(PKEY_Device_FriendlyName)), ctypes.POINTER(PROPVARIANT))(
            store, ctypes.byref(PKEY_Device_FriendlyName), ctypes.byref(prop)), "IPropertyStore.GetValue")
        return ctypes.wstring_at(prop.pwszVal) if prop.vt == VT_LPWSTR and prop.pwszVal else "Unnamed endpoint"
    finally:
        ctypes.windll.ole32.PropVariantClear(ctypes.byref(prop))
        release(store)


def _activate_client(device: LPVOID) -> LPVOID:
    client = LPVOID()
    check_hresult(_method(device, 3, HRESULT, ctypes.POINTER(type(IID_IAudioClient)), DWORD, LPVOID, ctypes.POINTER(LPVOID))(
        device, ctypes.byref(IID_IAudioClient), CLSCTX_ALL, None, ctypes.byref(client)), "IMMDevice.Activate(IAudioClient)")
    return client


def _mix_format(device: LPVOID) -> AudioFormat:
    client = _activate_client(device)
    raw = ctypes.POINTER(WAVEFORMATEX)()
    try:
        check_hresult(_method(client, 8, HRESULT, ctypes.POINTER(ctypes.POINTER(WAVEFORMATEX)))(client, ctypes.byref(raw)), "IAudioClient.GetMixFormat")
        return interpret_format(raw)
    finally:
        if raw:
            ctypes.windll.ole32.CoTaskMemFree(raw)
        release(client)


def enumerate_endpoints(flow: int) -> list[NativeEndpointInfo]:
    enumerator = collection = LPVOID()
    results: list[NativeEndpointInfo] = []
    try:
        enumerator = _create_enumerator()
        default_id = _safe_default_device_id(enumerator, flow)
        collection = LPVOID()
        check_hresult(_method(enumerator, 3, HRESULT, ctypes.c_int, DWORD, ctypes.POINTER(LPVOID))(
            enumerator, flow, DEVICE_STATE_ACTIVE, ctypes.byref(collection)), "IMMDeviceEnumerator.EnumAudioEndpoints")
        count = UINT32()
        check_hresult(_method(collection, 3, HRESULT, ctypes.POINTER(UINT32))(collection, ctypes.byref(count)), "IMMDeviceCollection.GetCount")
        for index in range(count.value):
            device = LPVOID()
            try:
                check_hresult(_method(collection, 4, HRESULT, UINT32, ctypes.POINTER(LPVOID))(collection, index, ctypes.byref(device)), "IMMDeviceCollection.Item")
                try:
                    fmt = _mix_format(device)
                except ValueError as exc:
                    print(f"Skipping endpoint {index}: {exc}", file=sys.stderr)
                    continue
                endpoint_id = _device_id(device)
                endpoint = Endpoint(endpoint_id, _friendly_name(device), fmt.channels, fmt.sample_rate,
                                    "render-loopback" if flow == eRender else "microphone", endpoint_id == default_id,
                                    fmt.channel_mask)
                results.append(NativeEndpointInfo(endpoint, flow))
            finally:
                release(device)
        return results
    finally:
        release(collection)
        release(enumerator)


class NativeWasapiStream:
    def __init__(self, endpoint_id: str, flow: int):
        self.apartment = ComApartment()
        self.apartment.__enter__()
        self.enumerator = self.device = self.client = self.capture = LPVOID()
        self.event: int | None = None
        self.pending = bytearray()
        self.started = False
        try:
            self.enumerator = _create_enumerator()
            self.device = LPVOID()
            check_hresult(_method(self.enumerator, 5, HRESULT, ctypes.c_wchar_p, ctypes.POINTER(LPVOID))(
                self.enumerator, endpoint_id, ctypes.byref(self.device)), "IMMDeviceEnumerator.GetDevice")
            self.client = _activate_client(self.device)
            raw = ctypes.POINTER(WAVEFORMATEX)()
            check_hresult(_method(self.client, 8, HRESULT, ctypes.POINTER(ctypes.POINTER(WAVEFORMATEX)))(self.client, ctypes.byref(raw)), "IAudioClient.GetMixFormat")
            try:
                native_format = interpret_format(raw)
                pcm_format = _pcm16_engine_format(native_format)
                closest = ctypes.POINTER(WAVEFORMATEX)()
                supported = _method(
                    self.client, 7, HRESULT, ctypes.c_int,
                    ctypes.POINTER(WAVEFORMATEX),
                    ctypes.POINTER(ctypes.POINTER(WAVEFORMATEX)),
                )(self.client, AUDCLNT_SHAREMODE_SHARED, ctypes.byref(pcm_format), ctypes.byref(closest))
                if closest:
                    ctypes.windll.ole32.CoTaskMemFree(closest)
                # S_OK means the engine accepts this exact PCM16 layout. S_FALSE
                # supplies a closest format, which is not safe to silently use.
                requested = ctypes.pointer(pcm_format) if supported == 0 else raw
                self.format = (AudioFormat(native_format.channels, native_format.sample_rate,
                                           16, 16, "pcm", native_format.channels * 2,
                                           native_format.channel_mask)
                               if supported == 0 else native_format)
                flags = AUDCLNT_STREAMFLAGS_EVENTCALLBACK | (AUDCLNT_STREAMFLAGS_LOOPBACK if flow == eRender else 0)
                check_hresult(_method(self.client, 3, HRESULT, ctypes.c_int, DWORD, ctypes.c_longlong, ctypes.c_longlong,
                                      ctypes.POINTER(WAVEFORMATEX), LPVOID)(
                    self.client, AUDCLNT_SHAREMODE_SHARED, flags, 0, 0, requested, None), "IAudioClient.Initialize")
            finally:
                ctypes.windll.ole32.CoTaskMemFree(raw)
            _, kernel32 = _require_windows()
            self.event = kernel32.CreateEventW(None, False, False, None)
            if not self.event:
                raise ctypes.WinError()
            check_hresult(_method(self.client, 13, HRESULT, LPVOID)(self.client, self.event), "IAudioClient.SetEventHandle")
            check_hresult(_method(self.client, 14, HRESULT, ctypes.POINTER(type(IID_IAudioCaptureClient)), ctypes.POINTER(LPVOID))(
                self.client, ctypes.byref(IID_IAudioCaptureClient), ctypes.byref(self.capture)), "IAudioClient.GetService(IAudioCaptureClient)")
            check_hresult(_method(self.client, 10, HRESULT)(self.client), "IAudioClient.Start")
            self.started = True
        except BaseException as exc:
            try:
                self.close()
            except BaseException as cleanup_error:
                raise exc from cleanup_error
            raise

    def read(self, frames: int, exception_on_overflow: bool = False) -> bytes:
        target = frames * self.format.channels * 2
        _, kernel32 = _require_windows()
        while len(self.pending) < target:
            wait = kernel32.WaitForSingleObject(self.event, 200)
            if wait == WAIT_TIMEOUT:
                # The timeout exists only to return control to the recorder so
                # it can observe shutdown. WASAPI packet frame counts, including
                # SILENT packets, are the sole source of recorded duration.
                return b""
            if wait != WAIT_OBJECT_0:
                raise ctypes.WinError()
            while len(self.pending) < target:
                available = UINT32()
                check_hresult(_method(self.capture, 5, HRESULT, ctypes.POINTER(UINT32))(self.capture, ctypes.byref(available)), "IAudioCaptureClient.GetNextPacketSize")
                if not available.value:
                    break
                data, packet_frames, flags = LPVOID(), UINT32(), DWORD()
                check_hresult(_method(self.capture, 3, HRESULT, ctypes.POINTER(LPVOID), ctypes.POINTER(UINT32), ctypes.POINTER(DWORD), LPVOID, LPVOID)(
                    self.capture, ctypes.byref(data), ctypes.byref(packet_frames), ctypes.byref(flags), None, None), "IAudioCaptureClient.GetBuffer")
                try:
                    silent = bool(flags.value & AUDCLNT_BUFFERFLAGS_SILENT)
                    raw = b"" if silent else ctypes.string_at(
                        data, packet_frames.value * self.format.block_align)
                    # Exact PCM16 negotiation makes this a single native copy.
                    # Conversion remains available for endpoints that reject it.
                    self.pending.extend(pcm16(raw, self.format, packet_frames.value, silent))
                finally:
                    check_hresult(_method(self.capture, 4, HRESULT, UINT32)(self.capture, packet_frames.value), "IAudioCaptureClient.ReleaseBuffer")
        result = bytes(self.pending[:target])
        del self.pending[:target]
        return result

    def stop_stream(self) -> None:
        if self.started:
            check_hresult(_method(self.client, 11, HRESULT)(self.client), "IAudioClient.Stop")
            self.started = False

    def close(self) -> None:
        first_error: BaseException | None = None

        def cleanup(action) -> None:
            nonlocal first_error
            try:
                action()
            except BaseException as exc:
                if first_error is None:
                    first_error = exc

        if self.started:
            cleanup(self.stop_stream)
        for pointer in (self.capture, self.client, self.device, self.enumerator):
            cleanup(lambda pointer=pointer: release(pointer))
        if self.event:
            event = self.event
            self.event = None
            cleanup(lambda: ctypes.windll.kernel32.CloseHandle(event))
        cleanup(lambda: self.apartment.__exit__(None, None, None))
        if first_error is not None:
            raise first_error


class NativeWasapiBackend:
    """Shared-mode endpoint loopback/capture backend with PCM16 output."""
    def endpoints(self) -> tuple[list[Endpoint], list[Endpoint]]:
        with ComApartment():
            return ([item.endpoint for item in enumerate_endpoints(eRender)],
                    [item.endpoint for item in enumerate_endpoints(eCapture)])

    def default_endpoints(self, renders: list[Endpoint] | None = None,
                          captures: list[Endpoint] | None = None) -> dict[str, Endpoint | None]:
        """Return role defaults for diagnostics without changing selection policy."""
        if renders is None or captures is None:
            renders, captures = self.endpoints()
        by_flow = {eRender: renders, eCapture: captures}
        roles = {"console": eConsole, "multimedia": eMultimedia,
                 "communications": eCommunications}
        result: dict[str, Endpoint | None] = {}
        with ComApartment():
            enumerator = _create_enumerator()
            try:
                for flow, prefix in ((eRender, "render"), (eCapture, "capture")):
                    for label, role in roles.items():
                        endpoint_id = _safe_default_device_id(enumerator, flow, role)
                        result[f"{label}_{prefix}"] = next(
                            (item for item in by_flow[flow] if str(item.index) == endpoint_id), None)
            finally:
                release(enumerator)
        return result

    def open_input(self, endpoint: Endpoint, frames_per_buffer: int) -> NativeWasapiStream:
        expected = eRender if endpoint.kind == "render-loopback" else eCapture
        return NativeWasapiStream(str(endpoint.index), expected)

    def sample_width(self) -> int:
        return 2

    def close(self) -> None:
        pass
