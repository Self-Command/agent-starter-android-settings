"""Build-time workaround for Agents 1.8.5 on the deployed non-AVX2 Xeon.

This pipeline explicitly uses the portable Silero ONNX plugin and external providers.
The framework nevertheless preloads livekit-local-inference, whose native extension
raises SIGILL on this host. Disable only that unused preload, keeping the SDK pinned.
The exact-version and exact-source guards require review on any future SDK upgrade.
"""
from importlib.metadata import distribution

package = distribution('livekit-agents')
assert package.version == '1.8.5', 'Review the CPU compatibility workaround before upgrading'
path = package.locate_file('livekit/agents/ipc/_preload.py')
source = path.read_text()
original = '_step("the local inference models", _local_inference_models)'
assert source.count(original) == 1, 'SDK preload source changed; review required'
path.write_text(source.replace(original, '# Native local inference unused; portable Silero ONNX is prewarmed by agent.py.'))
