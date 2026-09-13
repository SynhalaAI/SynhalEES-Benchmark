class RecorderProcessor extends AudioWorkletProcessor {
  process(inputs, outputs) {
    const input = inputs[0];
    if (input && input[0] && input[0].length > 0) {
      // Copy the Float32Array since the underlying buffer gets reused by the audio engine.
      const channelData = input[0].slice();
      this.port.postMessage(channelData, [channelData.buffer]);
    }

    // Pass audio straight through unchanged so downstream nodes (e.g. speakers) still work.
    const output = outputs[0];
    if (output && output[0] && input && input[0]) {
      output[0].set(input[0]);
    }

    return true; // keep the processor alive
  }
}

registerProcessor('recorder-processor', RecorderProcessor);
