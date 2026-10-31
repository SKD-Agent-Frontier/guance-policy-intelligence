import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../api/http";
import { catalogClient } from "../api/catalogClient";
import { extractPolicyVersionPin, regionTreeMatchesVersion } from "../api/policyFacets";
import type { PolicyFacetFilters, PolicyFacetsResponse, PolicyRegionsResponse, PolicyVersionPin } from "../types";

export type PolicyFacetsStatus = "loading" | "ready" | "unavailable" | "error";

export function usePolicyCatalogFacets() {
  const [facets, setFacets] = useState<PolicyFacetsResponse | null>(null);
  const [regions, setRegions] = useState<PolicyRegionsResponse | null>(null);
  const [status, setStatus] = useState<PolicyFacetsStatus>("loading");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const controller = useRef<AbortController | null>(null);
  const sequence = useRef(0);
  const lastFilters = useRef<PolicyFacetFilters>({});
  const unavailableRef = useRef(false);
  const pinRef = useRef<PolicyVersionPin | null>(null);
  const datasetMetaRef = useRef<{ total_documents: number; partial: boolean | null } | null>(null);

  const refresh = useCallback(async (
    filters: PolicyFacetFilters = lastFilters.current,
    resetPin = false,
  ): Promise<{ pin: PolicyVersionPin | null; status: PolicyFacetsStatus; dataset?: PolicyFacetsResponse["dataset"]; errorMessage?: string }> => {
    if (unavailableRef.current && !resetPin) return { pin: null, status: "unavailable" };
    controller.current?.abort();
    const requestController = new AbortController();
    controller.current = requestController;
    const requestSequence = ++sequence.current;
    lastFilters.current = filters;
    if (resetPin) {
      unavailableRef.current = false;
      pinRef.current = null;
      datasetMetaRef.current = null;
    }
    setStatus("loading");
    setErrorMessage(null);
    try {
      const response = await catalogClient.facets({ ...filters, ...(pinRef.current ?? {}) }, requestController.signal);
      if (requestSequence !== sequence.current) return { pin: null, status: "error" };
      const pin = extractPolicyVersionPin(response.dataset);
      if (!pin) throw new TypeError("维度响应缺少有效的数据版本，请重试。");
      if (pinRef.current && JSON.stringify(pinRef.current) !== JSON.stringify(pin)) {
        throw new Error("维度响应版本与当前检索不一致，请重新加载筛选项。");
      }
      if (datasetMetaRef.current
        && (datasetMetaRef.current.total_documents !== response.dataset.total_documents
          || datasetMetaRef.current.partial !== response.dataset.partial)) {
        throw new Error("同一数据版本的目录总数发生变化，请重新加载筛选项。");
      }
      pinRef.current = pin;
      datasetMetaRef.current = { total_documents: response.dataset.total_documents, partial: response.dataset.partial };
      setFacets(response);
      try {
        const tree = await catalogClient.regions(pin.region_version, requestController.signal);
        if (requestSequence !== sequence.current) return { pin: null, status: "error" };
        if (!regionTreeMatchesVersion(tree, pin)) throw new Error("地区树版本与维度计数不一致，请重新加载筛选项。");
        setRegions(tree);
      } catch (error) {
        if (requestSequence !== sequence.current || requestController.signal.aborted) return { pin: null, status: "error" };
        setRegions(null);
        const treeUnavailable = error instanceof ApiError && (error.status === 404 || error.status === 503);
        setErrorMessage(error instanceof Error ? error.message : "完整地区树暂时不可用，请重新加载筛选项。");
        setStatus(treeUnavailable ? "unavailable" : "error");
        return { pin: treeUnavailable ? pin : null, status: treeUnavailable ? "unavailable" : "error", dataset: response.dataset, errorMessage: error instanceof Error ? error.message : undefined };
      }
      setStatus("ready");
      return { pin, status: "ready", dataset: response.dataset };
    } catch (error) {
      if (requestSequence !== sequence.current || requestController.signal.aborted) return { pin: null, status: "error" };
      setFacets(null);
      setRegions(null);
      const bootstrapUnsupported = error instanceof ApiError && error.status === 400
        && Object.keys(filters).length === 0 && pinRef.current === null;
      if (error instanceof ApiError && (error.status === 503 || error.status === 404) || bootstrapUnsupported) {
        if (!pinRef.current) unavailableRef.current = true;
        setStatus("unavailable");
        setErrorMessage("维度筛选暂不可用，旧目录与原文仍可继续使用。");
        return { pin: null, status: "unavailable", errorMessage: error.message };
      } else if (error instanceof ApiError && error.status === 409) {
        setStatus("error");
        setErrorMessage("数据版本已更新，请重新加载筛选项后再检索。");
      } else {
        setStatus("error");
        setErrorMessage(error instanceof Error ? error.message : "维度筛选暂时无法访问，请重试。");
      }
      return { pin: null, status: "error", errorMessage: error instanceof Error ? error.message : undefined };
    }
  }, []);

  useEffect(() => {
    return () => {
      sequence.current += 1;
      controller.current?.abort();
    };
  }, []);

  const reload = useCallback((filters: PolicyFacetFilters = lastFilters.current) => refresh(filters, true), [refresh]);

  return { facets, regions, status, errorMessage, refresh, reload };
}
