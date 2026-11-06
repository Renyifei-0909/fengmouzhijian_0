import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation, useNavigate } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiRequestError } from "../lib/api";
import type { EngineeringObject, ProjectGisSummary, WorkOrder } from "../lib/api";
import { GISMapPage } from "./GISMapPage";

vi.mock("../components/gis/GeoMap", () => ({ GeoMap: () => <div /> }));

const timestamp = "2026-11-05T16:30:00Z";
const first = makeObject("obj-a1", "project-a", "支线区段");
const target = makeObject("obj-a2", "project-a", "目标区段");
const other = makeObject("obj-b1", "project-b", "滨河区段");

function makeObject(id: string, projectId: string, name: string): EngineeringObject {
  return {
    id,
    project_id: projectId,
    design_package_id: "design",
    object_code: id,
    name,
    object_type: "pipe_route",
    source_layer: "pipe_routes",
    source_feature_id: "1",
    geometry_type: "LineString",
    geometry_wgs84: { type: "LineString", coordinates: [[120, 32], [121, 33]] },
    geometry_source_crs_epsg: 4326,
    attributes_snapshot: {},
    expected_rules: {},
    design_version: "1",
    created_at: timestamp,
  };
}

function makeOrder(object: EngineeringObject, status = "assigned"): WorkOrder {
  return {
    id: "order-a",
    project_id: object.project_id,
    engineering_object_id: object.id,
    baseline_id: "baseline-a",
    work_order_code: "区段工单",
    procedure_code: "TRENCH-BEFORE-BACKFILL",
    status,
    design_version: "1",
    design_snapshot: {},
    geometry_snapshot: {},
    rules_snapshot: {},
    spatial_tolerance_m: 50,
    gps_accuracy_threshold_m: 30,
    assigned_to: status === "assigned" ? "施工班组" : null,
    notes: null,
    created_at: timestamp,
    updated_at: timestamp,
  };
}

function makeSummary(id: string, objects: EngineeringObject[], orders: WorkOrder[] = []): ProjectGisSummary {
  return {
    project_id: id,
    design_package_count: 1,
    engineering_object_count: objects.length,
    work_order_count: orders.length,
    objects,
    work_orders: orders,
    truth_note: "",
  };
}

function RouteProbe() {
  const navigate = useNavigate();
  const { search } = useLocation();
  return <><output aria-label="当前路径">{search}</output><button onClick={() => void navigate(-1)}>上一个页面</button></>;
}

function open(search: string) {
  return render(
    <MemoryRouter initialEntries={[`/gis-map${search}`]}>
      <GISMapPage />
      <RouteProbe />
    </MemoryRouter>,
  );
}

describe("工程作业项目与工单选择", () => {
  beforeEach(() => {
    vi.spyOn(api, "listProjects").mockResolvedValue([
      { id: "project-a", code: "A", name: "滨河一区", location: "", manager: null, status: "active", created_at: timestamp },
      { id: "project-b", code: "B", name: "滨河二区", location: "", manager: null, status: "active", created_at: timestamp },
    ]);
    vi.spyOn(api, "listDesignPackages").mockResolvedValue([]);
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("uses the linked object for a work-order deep link", async () => {
    const linked = { ...makeOrder(other), id: "order-b" };
    vi.spyOn(api, "getWorkOrder").mockResolvedValue(linked);
    vi.spyOn(api, "projectGisSummary").mockImplementation(async (projectId) => projectId === "project-b"
      ? makeSummary("project-b", [other], [linked])
      : makeSummary("project-a", [first, target]));
    open("?workOrder=order-b");

    const inspector = screen.getByRole("complementary", { name: "对象详情" });
    await within(inspector).findByRole("heading", { name: "滨河区段" });
    expect(within(inspector).queryByRole("heading", { name: "支线区段" })).toBeNull();
    await waitFor(() => {
      expect(screen.getByLabelText("当前路径")).toHaveTextContent("project=project-b");
      expect(screen.getByLabelText("当前路径")).toHaveTextContent("object=obj-b1");
    });
    expect(screen.getByRole("link", { name: "工单详情" })).toHaveAttribute("href", "/work-orders/order-b");
  });

  it("ignores late data from the previous project and restores it on Back", async () => {
    let returnOld!: (summary: ProjectGisSummary) => void;
    const older = new Promise<ProjectGisSummary>((resolve) => { returnOld = resolve; });
    const summary = vi.spyOn(api, "projectGisSummary")
      .mockImplementation((id) => Promise.resolve(id === "project-b"
        ? makeSummary(id, [other])
        : makeSummary(id, [first])))
      .mockReturnValueOnce(older);
    open("?project=project-a");

    await waitFor(() => expect(summary).toHaveBeenCalledWith("project-a"));
    const select = screen.getByRole("combobox", { name: "当前项目" });
    await userEvent.selectOptions(select, "project-b");
    await screen.findByRole("button", { name: /滨河区段/ });
    await act(async () => { returnOld(makeSummary("project-a", [first])); });
    expect(screen.queryByRole("button", { name: /支线区段/ })).toBeNull();
    expect(select).toHaveValue("project-b");

    await userEvent.click(screen.getByRole("button", { name: "上一个页面" }));
    await screen.findByRole("button", { name: /支线区段/ });
    expect(select).toHaveValue("project-a");
  });

  it("retains the saved draft when assignment fails", async () => {
    let orders: WorkOrder[] = [];
    const draft = makeOrder(target, "draft");
    vi.spyOn(api, "projectGisSummary").mockImplementation(async () => makeSummary("project-a", [target], orders));
    const create = vi.spyOn(api, "createWorkOrder").mockImplementation(async () => {
      orders = [draft];
      return draft;
    });
    const assign = vi.spyOn(api, "assignWorkOrder").mockRejectedValue(new ApiRequestError("分派未完成", 500));

    open("?project=project-a&object=obj-a2");
    await userEvent.click(await screen.findByRole("button", { name: "新建工单" }));
    const drawer = screen.getByRole("dialog", { name: "新建施工工单" });
    await userEvent.click(within(drawer).getByRole("button", { name: "创建并分派" }));

    expect(create).toHaveBeenCalledTimes(1);
    expect(assign).toHaveBeenCalledWith(draft.id, "现场采集");
    await screen.findByText(/工单 区段工单 已保存。分派未确认/);
    expect(await screen.findByRole("link", { name: "分派工单" })).toHaveAttribute("href", "/work-orders/order-a");
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
