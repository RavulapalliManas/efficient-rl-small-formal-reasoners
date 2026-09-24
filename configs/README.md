# Config ownership

Keep model, data, pretraining, mid-training, RL, reward, curriculum, and evaluation settings in the matching directories. Add a runnable config only with its entry point. Every expensive run must save the full resolved config and seed under `artifacts/<run-id>/`. No runnable config exists yet.
