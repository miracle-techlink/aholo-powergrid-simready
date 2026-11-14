# Third-party software and data

MIT covers this repository's original code; it does not relicense external SDKs, model weights, videos or reconstruction assets.

- NVIDIA Isaac Sim and referenced container images are obtained separately under NVIDIA's applicable license/EULA; no SDK is distributed in this source package.
- Aholo is an optional external reconstruction service. Credentials, submitted inputs and returned assets remain subject to the provider's terms.
- Segmentation and reconstruction backends, including Grounding DINO, SAM and externally configured baselines, retain their upstream software and model licenses. No model weights are included.
- The NeRF2Physics numerical adaptation cites its upstream source and scope in docs/nerf2physics.md. Synthetic fixtures do not represent new visual-model inference or real measured properties.

The aloho-hackson branch retains the earlier full project's notices for its robot demonstrations, captured scenes and historical materials.
