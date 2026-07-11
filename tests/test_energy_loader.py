"""CSV 载入与脏数据拒绝测试。"""

from __future__ import annotations

from pathlib import Path

from custom_components.llama_conversation.edgehome.energy.loader import EnergyDatasetLoader


def test_energy_loader_reads_generated_dataset_and_deduplicates_bad_rows(tmp_path) -> None:
    dataset = Path("datasets/energy/abnormal_week.csv")
    result = EnergyDatasetLoader().load(dataset)

    assert result.success is True
    assert len(result.records) == 1176
    assert result.rejected_rows == 0

    broken = tmp_path / "broken.csv"
    broken.write_text(
        "timestamp,entity_id,device_name,room,power_w,energy_kwh,state,occupancy,temperature,scene\n"
        "2026-01-01T00:00:00+00:00,switch.edgehome_test_socket,插座,home,0,0,on,false,25,test\n"
        "2026-01-01T00:00:00+00:00,switch.edgehome_test_socket,插座,home,0,0,on,false,25,test\n"
        "2026-01-01T01:00:00+00:00,switch.edgehome_test_socket,插座,home,-1,0,on,false,25,test\n"
        "2026-01-01T02:00:00+00:00,switch.edgehome_test_socket,插座,home,1,0,,false,25,test\n",
        encoding="utf-8",
    )
    checked = EnergyDatasetLoader().load(broken)

    assert len(checked.records) == 1
    assert checked.duplicate_rows == 1
    assert checked.rejected_rows == 2
