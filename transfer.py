'''Main transfer script.'''

import hydra


@hydra.main(config_path='conf', config_name='transfer', version_base='1.1')
def run(config):
    # Deferred imports for faster tab completion
    import os

    import flatten_dict
    import pytorch_lightning as pl

    from src.datasets.catalog import TRANSFER_DATASETS
    from src.systems import transfer

    pl.seed_everything(config.trainer.seed)

    # Saving checkpoints and logging with wandb.
    flat_config = flatten_dict.flatten(config, reducer='dot')
    save_dir = os.path.join(config.exp.base_dir, config.exp.name)
    wandb_logger = pl.loggers.WandbLogger(project='domain-agnostic', name=config.exp.name)
    wandb_logger.log_hyperparams(flat_config)
    # Check every validation epoch, and save best model based on maximizing the transfer/val_metric.
    ckpt_callback = pl.callbacks.ModelCheckpoint(
        dirpath=save_dir,
        filename='best_model_{epoch}',
        monitor='transfer/val_metric',
        save_top_k=1,
        mode='max'
    )

    assert config.dataset.name in TRANSFER_DATASETS, f'{config.dataset.name} not one of {TRANSFER_DATASETS}.'
    # PyTorch Lightning Trainer.
    trainer = pl.Trainer(
        default_root_dir=save_dir,
        logger=wandb_logger,
        gpus=str(config.gpus),
        max_epochs=config.trainer.max_epochs,
        min_epochs=config.trainer.max_epochs,
        val_check_interval=config.trainer.val_check_interval,
        limit_val_batches=config.trainer.limit_val_batches,
        callbacks=[ckpt_callback],
        # weights_summary=config.trainer.weights_summary,
        precision=config.trainer.precision,
        # accelerator="ddp"
    )

    system = transfer.TransferSystem(config)

    if not config.test:
        trainer.fit(system)
    else:
        trainer.test(system)


if __name__ == '__main__':
    run()
