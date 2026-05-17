/*
 * Copyright (C) 2014 Patrick Mours
 * SPDX-License-Identifier: BSD-3-Clause OR MIT
 */

#include "d3d12_device.hpp"
#include "d3d12_resource.hpp"
#include "dxgi/dxgi_adapter.hpp"
#include "dll_log.hpp" // Include late to get 'hr_to_string' helper function
#include "com_utils.hpp"
#include "hook_manager.hpp"
#include "addon_manager.hpp"

#include <atomic>

std::shared_mutex g_d3d12_adapter_mutex;

extern thread_local bool g_in_dxgi_runtime;

namespace
{
	constexpr uint32_t GPMI_RAW_LOG_LIMIT = 12000;
	std::atomic_uint32_t s_gpmi_raw_device_log_count = 0;
	std::atomic_uint32_t s_gpmi_raw_command_log_count = 0;
	std::atomic_uint32_t s_gpmi_raw_view_log_count = 0;

	bool gpmi_should_log(std::atomic_uint32_t &counter)
	{
		return counter.fetch_add(1, std::memory_order_relaxed) < GPMI_RAW_LOG_LIMIT;
	}

	void gpmi_log_resource_desc(const char *prefix, const D3D12_RESOURCE_DESC *desc)
	{
		if (desc == nullptr)
		{
			reshade::log::message(reshade::log::level::info, "[GPMI runtime] %s desc=null", prefix);
			return;
		}

		reshade::log::message(
			reshade::log::level::info,
			"[GPMI runtime] %s dim=%u size=%llux%u depth_or_array=%u mips=%u fmt=%u layout=%u flags=0x%x samples=%u/%u",
			prefix,
			static_cast<unsigned>(desc->Dimension),
			static_cast<unsigned long long>(desc->Width),
			desc->Height,
			desc->DepthOrArraySize,
			desc->MipLevels,
			static_cast<unsigned>(desc->Format),
			static_cast<unsigned>(desc->Layout),
			static_cast<unsigned>(desc->Flags),
			desc->SampleDesc.Count,
			desc->SampleDesc.Quality);
	}

	void gpmi_log_resource_desc1(const char *prefix, const D3D12_RESOURCE_DESC1 *desc)
	{
		if (desc == nullptr)
		{
			reshade::log::message(reshade::log::level::info, "[GPMI runtime] %s desc1=null", prefix);
			return;
		}

		reshade::log::message(
			reshade::log::level::info,
			"[GPMI runtime] %s dim=%u size=%llux%u depth_or_array=%u mips=%u fmt=%u layout=%u flags=0x%x samples=%u/%u sampler_region=%ux%ux%u",
			prefix,
			static_cast<unsigned>(desc->Dimension),
			static_cast<unsigned long long>(desc->Width),
			desc->Height,
			desc->DepthOrArraySize,
			desc->MipLevels,
			static_cast<unsigned>(desc->Format),
			static_cast<unsigned>(desc->Layout),
			static_cast<unsigned>(desc->Flags),
			desc->SampleDesc.Count,
			desc->SampleDesc.Quality,
			desc->SamplerFeedbackMipRegion.Width,
			desc->SamplerFeedbackMipRegion.Height,
			desc->SamplerFeedbackMipRegion.Depth);
	}

	void gpmi_log_box(const char *prefix, const D3D12_BOX *box)
	{
		if (box == nullptr)
		{
			reshade::log::message(reshade::log::level::info, "[GPMI runtime] %s box=null", prefix);
			return;
		}

		reshade::log::message(
			reshade::log::level::info,
			"[GPMI runtime] %s left=%u top=%u front=%u right=%u bottom=%u back=%u size=%ux%ux%u",
			prefix,
			box->left,
			box->top,
			box->front,
			box->right,
			box->bottom,
			box->back,
			box->right >= box->left ? box->right - box->left : 0u,
			box->bottom >= box->top ? box->bottom - box->top : 0u,
			box->back >= box->front ? box->back - box->front : 0u);
	}

	void gpmi_log_texture_copy_location(const char *prefix, const D3D12_TEXTURE_COPY_LOCATION *location)
	{
		if (location == nullptr)
		{
			reshade::log::message(reshade::log::level::info, "[GPMI runtime] %s location=null", prefix);
			return;
		}

		const bool is_placed = location->Type == D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT;
		const UINT footprint_width = is_placed ? location->PlacedFootprint.Footprint.Width : 0u;
		const UINT footprint_height = is_placed ? location->PlacedFootprint.Footprint.Height : 0u;
		const UINT footprint_depth = is_placed ? location->PlacedFootprint.Footprint.Depth : 0u;
		const UINT footprint_format = is_placed ? static_cast<unsigned>(location->PlacedFootprint.Footprint.Format) : 0u;
		const UINT footprint_row_pitch = is_placed ? location->PlacedFootprint.Footprint.RowPitch : 0u;

		reshade::log::message(
			reshade::log::level::info,
			"[GPMI runtime] %s resource=%p type=%u subresource=%u placed_offset=%llu footprint=%ux%ux%u footprint_fmt=%u row_pitch=%u",
			prefix,
			location->pResource,
			static_cast<unsigned>(location->Type),
			location->Type == D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX ? location->SubresourceIndex : 0u,
			is_placed ? static_cast<unsigned long long>(location->PlacedFootprint.Offset) : 0ull,
			footprint_width,
			footprint_height,
			footprint_depth,
			footprint_format,
			footprint_row_pitch);
	}

#if RESHADE_ADDON >= 2
	void gpmi_install_resource_map_hooks(ID3D12Resource *resource)
	{
		if (resource == nullptr)
			return;

		auto vtable = reshade::hooks::vtable_from_instance(resource);
		reshade::hooks::install("GPMI ID3D12Resource::Map", vtable, 8, &ID3D12Resource_Map);
		reshade::hooks::install("GPMI ID3D12Resource::Unmap", vtable, 9, &ID3D12Resource_Unmap);
	}
#endif

	void gpmi_install_command_list_hooks(ID3D12GraphicsCommandList *cmd);

	void STDMETHODCALLTYPE gpmi_cmd_DrawInstanced(ID3D12GraphicsCommandList *cmd, UINT vertex_count, UINT instance_count, UINT start_vertex, UINT start_instance)
	{
		if (gpmi_should_log(s_gpmi_raw_command_log_count))
			reshade::log::message(reshade::log::level::info, "[GPMI runtime] RAW D3D12 DrawInstanced cmd=%p vertex_count=%u instance_count=%u start_vertex=%u start_instance=%u", cmd, vertex_count, instance_count, start_vertex, start_instance);
		return reshade::hooks::call_vtable<12, void>(cmd, vertex_count, instance_count, start_vertex, start_instance);
	}

	void STDMETHODCALLTYPE gpmi_cmd_DrawIndexedInstanced(ID3D12GraphicsCommandList *cmd, UINT index_count, UINT instance_count, UINT start_index, INT base_vertex, UINT start_instance)
	{
		if (gpmi_should_log(s_gpmi_raw_command_log_count))
			reshade::log::message(reshade::log::level::info, "[GPMI runtime] RAW D3D12 DrawIndexedInstanced cmd=%p index_count=%u instance_count=%u start_index=%u base_vertex=%d start_instance=%u", cmd, index_count, instance_count, start_index, base_vertex, start_instance);
		return reshade::hooks::call_vtable<13, void>(cmd, index_count, instance_count, start_index, base_vertex, start_instance);
	}

	void STDMETHODCALLTYPE gpmi_cmd_Dispatch(ID3D12GraphicsCommandList *cmd, UINT x, UINT y, UINT z)
	{
		if (gpmi_should_log(s_gpmi_raw_command_log_count))
			reshade::log::message(reshade::log::level::info, "[GPMI runtime] RAW D3D12 Dispatch cmd=%p groups=%ux%ux%u", cmd, x, y, z);
		return reshade::hooks::call_vtable<14, void>(cmd, x, y, z);
	}

	void STDMETHODCALLTYPE gpmi_cmd_CopyBufferRegion(ID3D12GraphicsCommandList *cmd, ID3D12Resource *dst, UINT64 dst_offset, ID3D12Resource *src, UINT64 src_offset, UINT64 bytes)
	{
		if (gpmi_should_log(s_gpmi_raw_command_log_count))
			reshade::log::message(reshade::log::level::info, "[GPMI runtime] RAW D3D12 CopyBufferRegion cmd=%p dst=%p dst_off=%llu src=%p src_off=%llu bytes=%llu", cmd, dst, static_cast<unsigned long long>(dst_offset), src, static_cast<unsigned long long>(src_offset), static_cast<unsigned long long>(bytes));
		return reshade::hooks::call_vtable<15, void>(cmd, dst, dst_offset, src, src_offset, bytes);
	}

	void STDMETHODCALLTYPE gpmi_cmd_CopyTextureRegion(ID3D12GraphicsCommandList *cmd, const D3D12_TEXTURE_COPY_LOCATION *dst, UINT dst_x, UINT dst_y, UINT dst_z, const D3D12_TEXTURE_COPY_LOCATION *src, const D3D12_BOX *src_box)
	{
		if (gpmi_should_log(s_gpmi_raw_command_log_count))
		{
			reshade::log::message(reshade::log::level::info, "[GPMI runtime] RAW D3D12 CopyTextureRegion cmd=%p dst=%p dst_type=%u dst_sub=%u src=%p src_type=%u src_sub=%u dst_xyz=%u,%u,%u box=%p", cmd,
				dst != nullptr ? dst->pResource : nullptr,
				dst != nullptr ? static_cast<unsigned>(dst->Type) : 0u,
				dst != nullptr && dst->Type == D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX ? dst->SubresourceIndex : 0u,
				src != nullptr ? src->pResource : nullptr,
				src != nullptr ? static_cast<unsigned>(src->Type) : 0u,
				src != nullptr && src->Type == D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX ? src->SubresourceIndex : 0u,
				dst_x, dst_y, dst_z, src_box);
			if (dst != nullptr && dst->pResource != nullptr) { const D3D12_RESOURCE_DESC desc = dst->pResource->GetDesc(); gpmi_log_resource_desc("RAW CopyTextureRegion dst", &desc); }
			if (src != nullptr && src->pResource != nullptr) { const D3D12_RESOURCE_DESC desc = src->pResource->GetDesc(); gpmi_log_resource_desc("RAW CopyTextureRegion src", &desc); }
			gpmi_log_texture_copy_location("RAW CopyTextureRegion dst_location", dst);
			gpmi_log_texture_copy_location("RAW CopyTextureRegion src_location", src);
			gpmi_log_box("RAW CopyTextureRegion src_box", src_box);
		}
		return reshade::hooks::call_vtable<16, void>(cmd, dst, dst_x, dst_y, dst_z, src, src_box);
	}

	void STDMETHODCALLTYPE gpmi_cmd_CopyResource(ID3D12GraphicsCommandList *cmd, ID3D12Resource *dst, ID3D12Resource *src)
	{
		if (gpmi_should_log(s_gpmi_raw_command_log_count))
		{
			reshade::log::message(reshade::log::level::info, "[GPMI runtime] RAW D3D12 CopyResource cmd=%p dst=%p src=%p", cmd, dst, src);
			if (dst != nullptr) { const D3D12_RESOURCE_DESC desc = dst->GetDesc(); gpmi_log_resource_desc("RAW CopyResource dst", &desc); }
			if (src != nullptr) { const D3D12_RESOURCE_DESC desc = src->GetDesc(); gpmi_log_resource_desc("RAW CopyResource src", &desc); }
		}
		return reshade::hooks::call_vtable<17, void>(cmd, dst, src);
	}

	void STDMETHODCALLTYPE gpmi_cmd_ResourceBarrier(ID3D12GraphicsCommandList *cmd, UINT num_barriers, const D3D12_RESOURCE_BARRIER *barriers)
	{
		if (gpmi_should_log(s_gpmi_raw_command_log_count))
			reshade::log::message(reshade::log::level::info, "[GPMI runtime] RAW D3D12 ResourceBarrier cmd=%p count=%u first_resource=%p first_type=%u", cmd, num_barriers, num_barriers != 0 && barriers != nullptr ? barriers[0].Transition.pResource : nullptr, num_barriers != 0 && barriers != nullptr ? static_cast<unsigned>(barriers[0].Type) : 0u);
		return reshade::hooks::call_vtable<26, void>(cmd, num_barriers, barriers);
	}

	void STDMETHODCALLTYPE gpmi_cmd_SetDescriptorHeaps(ID3D12GraphicsCommandList *cmd, UINT num_heaps, ID3D12DescriptorHeap *const *heaps)
	{
		if (gpmi_should_log(s_gpmi_raw_command_log_count))
			reshade::log::message(reshade::log::level::info, "[GPMI runtime] RAW D3D12 SetDescriptorHeaps cmd=%p count=%u first_heap=%p", cmd, num_heaps, num_heaps != 0 && heaps != nullptr ? heaps[0] : nullptr);
		return reshade::hooks::call_vtable<28, void>(cmd, num_heaps, heaps);
	}

	void STDMETHODCALLTYPE gpmi_cmd_SetGraphicsRootSignature(ID3D12GraphicsCommandList *cmd, ID3D12RootSignature *root_sig)
	{
		if (gpmi_should_log(s_gpmi_raw_command_log_count))
			reshade::log::message(reshade::log::level::info, "[GPMI runtime] RAW D3D12 SetGraphicsRootSignature cmd=%p root=%p", cmd, root_sig);
		return reshade::hooks::call_vtable<30, void>(cmd, root_sig);
	}

	void STDMETHODCALLTYPE gpmi_cmd_SetComputeRootDescriptorTable(ID3D12GraphicsCommandList *cmd, UINT root_index, D3D12_GPU_DESCRIPTOR_HANDLE base_descriptor)
	{
		if (gpmi_should_log(s_gpmi_raw_command_log_count))
			reshade::log::message(reshade::log::level::info, "[GPMI runtime] RAW D3D12 SetComputeRootDescriptorTable cmd=%p root=%u gpu_handle=0x%llx", cmd, root_index, static_cast<unsigned long long>(base_descriptor.ptr));
		return reshade::hooks::call_vtable<31, void>(cmd, root_index, base_descriptor);
	}

	void STDMETHODCALLTYPE gpmi_cmd_SetGraphicsRootDescriptorTable(ID3D12GraphicsCommandList *cmd, UINT root_index, D3D12_GPU_DESCRIPTOR_HANDLE base_descriptor)
	{
		if (gpmi_should_log(s_gpmi_raw_command_log_count))
			reshade::log::message(reshade::log::level::info, "[GPMI runtime] RAW D3D12 SetGraphicsRootDescriptorTable cmd=%p root=%u gpu_handle=0x%llx", cmd, root_index, static_cast<unsigned long long>(base_descriptor.ptr));
		return reshade::hooks::call_vtable<32, void>(cmd, root_index, base_descriptor);
	}

	void STDMETHODCALLTYPE gpmi_cmd_SetGraphicsRootConstantBufferView(ID3D12GraphicsCommandList *cmd, UINT root_index, D3D12_GPU_VIRTUAL_ADDRESS address)
	{
		if (gpmi_should_log(s_gpmi_raw_command_log_count))
			reshade::log::message(reshade::log::level::info, "[GPMI runtime] RAW D3D12 SetGraphicsRootConstantBufferView cmd=%p root=%u gpu_va=0x%llx", cmd, root_index, static_cast<unsigned long long>(address));
		return reshade::hooks::call_vtable<38, void>(cmd, root_index, address);
	}

	void STDMETHODCALLTYPE gpmi_cmd_SetGraphicsRootShaderResourceView(ID3D12GraphicsCommandList *cmd, UINT root_index, D3D12_GPU_VIRTUAL_ADDRESS address)
	{
		if (gpmi_should_log(s_gpmi_raw_command_log_count))
			reshade::log::message(reshade::log::level::info, "[GPMI runtime] RAW D3D12 SetGraphicsRootShaderResourceView cmd=%p root=%u gpu_va=0x%llx", cmd, root_index, static_cast<unsigned long long>(address));
		return reshade::hooks::call_vtable<40, void>(cmd, root_index, address);
	}

	void STDMETHODCALLTYPE gpmi_cmd_SetGraphicsRootUnorderedAccessView(ID3D12GraphicsCommandList *cmd, UINT root_index, D3D12_GPU_VIRTUAL_ADDRESS address)
	{
		if (gpmi_should_log(s_gpmi_raw_command_log_count))
			reshade::log::message(reshade::log::level::info, "[GPMI runtime] RAW D3D12 SetGraphicsRootUnorderedAccessView cmd=%p root=%u gpu_va=0x%llx", cmd, root_index, static_cast<unsigned long long>(address));
		return reshade::hooks::call_vtable<42, void>(cmd, root_index, address);
	}

	void STDMETHODCALLTYPE gpmi_cmd_BeginRenderPass(ID3D12GraphicsCommandList4 *cmd, UINT rt_count, const D3D12_RENDER_PASS_RENDER_TARGET_DESC *rts, const D3D12_RENDER_PASS_DEPTH_STENCIL_DESC *ds, D3D12_RENDER_PASS_FLAGS flags)
	{
		if (gpmi_should_log(s_gpmi_raw_command_log_count))
			reshade::log::message(reshade::log::level::info, "[GPMI runtime] RAW D3D12 BeginRenderPass cmd=%p rt_count=%u flags=0x%x", cmd, rt_count, static_cast<unsigned>(flags));
		return reshade::hooks::call_vtable<68, void>(cmd, rt_count, rts, ds, flags);
	}

	void STDMETHODCALLTYPE gpmi_cmd_EndRenderPass(ID3D12GraphicsCommandList4 *cmd)
	{
		if (gpmi_should_log(s_gpmi_raw_command_log_count))
			reshade::log::message(reshade::log::level::info, "[GPMI runtime] RAW D3D12 EndRenderPass cmd=%p", cmd);
		return reshade::hooks::call_vtable<69, void>(cmd);
	}

	void gpmi_install_command_list_hooks(ID3D12GraphicsCommandList *cmd)
	{
		if (cmd == nullptr)
			return;
		auto vtable = reshade::hooks::vtable_from_instance(cmd);
		reshade::hooks::install("GPMI ID3D12GraphicsCommandList::DrawInstanced", vtable, 12, &gpmi_cmd_DrawInstanced);
		reshade::hooks::install("GPMI ID3D12GraphicsCommandList::DrawIndexedInstanced", vtable, 13, &gpmi_cmd_DrawIndexedInstanced);
		reshade::hooks::install("GPMI ID3D12GraphicsCommandList::Dispatch", vtable, 14, &gpmi_cmd_Dispatch);
		reshade::hooks::install("GPMI ID3D12GraphicsCommandList::CopyBufferRegion", vtable, 15, &gpmi_cmd_CopyBufferRegion);
		reshade::hooks::install("GPMI ID3D12GraphicsCommandList::CopyTextureRegion", vtable, 16, &gpmi_cmd_CopyTextureRegion);
		reshade::hooks::install("GPMI ID3D12GraphicsCommandList::CopyResource", vtable, 17, &gpmi_cmd_CopyResource);
		reshade::hooks::install("GPMI ID3D12GraphicsCommandList::ResourceBarrier", vtable, 26, &gpmi_cmd_ResourceBarrier);
		reshade::hooks::install("GPMI ID3D12GraphicsCommandList::SetDescriptorHeaps", vtable, 28, &gpmi_cmd_SetDescriptorHeaps);
		reshade::hooks::install("GPMI ID3D12GraphicsCommandList::SetGraphicsRootSignature", vtable, 30, &gpmi_cmd_SetGraphicsRootSignature);
		reshade::hooks::install("GPMI ID3D12GraphicsCommandList::SetComputeRootDescriptorTable", vtable, 31, &gpmi_cmd_SetComputeRootDescriptorTable);
		reshade::hooks::install("GPMI ID3D12GraphicsCommandList::SetGraphicsRootDescriptorTable", vtable, 32, &gpmi_cmd_SetGraphicsRootDescriptorTable);
		reshade::hooks::install("GPMI ID3D12GraphicsCommandList::SetGraphicsRootConstantBufferView", vtable, 38, &gpmi_cmd_SetGraphicsRootConstantBufferView);
		reshade::hooks::install("GPMI ID3D12GraphicsCommandList::SetGraphicsRootShaderResourceView", vtable, 40, &gpmi_cmd_SetGraphicsRootShaderResourceView);
		reshade::hooks::install("GPMI ID3D12GraphicsCommandList::SetGraphicsRootUnorderedAccessView", vtable, 42, &gpmi_cmd_SetGraphicsRootUnorderedAccessView);
		reshade::hooks::install("GPMI ID3D12GraphicsCommandList4::BeginRenderPass", vtable, 68, &gpmi_cmd_BeginRenderPass);
		reshade::hooks::install("GPMI ID3D12GraphicsCommandList4::EndRenderPass", vtable, 69, &gpmi_cmd_EndRenderPass);
	}

	HRESULT STDMETHODCALLTYPE gpmi_dev_CreateCommandList(ID3D12Device *device, UINT node_mask, D3D12_COMMAND_LIST_TYPE type, ID3D12CommandAllocator *allocator, ID3D12PipelineState *initial_state, REFIID riid, void **pp_command_list)
	{
		if (gpmi_should_log(s_gpmi_raw_device_log_count))
			reshade::log::message(reshade::log::level::info, "[GPMI runtime] RAW D3D12 CreateCommandList device=%p node=%u type=%u allocator=%p initial_state=%p riid=%s pp=%p", device, node_mask, static_cast<unsigned>(type), allocator, initial_state, reshade::log::iid_to_string(riid).c_str(), pp_command_list);

		const HRESULT hr = reshade::hooks::call_vtable<12, HRESULT>(device, node_mask, type, allocator, initial_state, riid, pp_command_list);

		if (gpmi_should_log(s_gpmi_raw_device_log_count))
			reshade::log::message(reshade::log::level::info, "[GPMI runtime] RAW D3D12 CreateCommandList result hr=%s raw=%p", reshade::log::hr_to_string(hr).c_str(), SUCCEEDED(hr) && pp_command_list != nullptr ? *pp_command_list : nullptr);
		if (SUCCEEDED(hr) && pp_command_list != nullptr && *pp_command_list != nullptr && type >= D3D12_COMMAND_LIST_TYPE_DIRECT && type <= D3D12_COMMAND_LIST_TYPE_COPY)
			gpmi_install_command_list_hooks(static_cast<ID3D12GraphicsCommandList *>(*pp_command_list));
		return hr;
	}

	HRESULT STDMETHODCALLTYPE gpmi_dev_CreateCommandList1(ID3D12Device4 *device, UINT node_mask, D3D12_COMMAND_LIST_TYPE type, D3D12_COMMAND_LIST_FLAGS flags, REFIID riid, void **pp_command_list)
	{
		if (gpmi_should_log(s_gpmi_raw_device_log_count))
			reshade::log::message(reshade::log::level::info, "[GPMI runtime] RAW D3D12 CreateCommandList1 device=%p node=%u type=%u flags=0x%x riid=%s pp=%p", device, node_mask, static_cast<unsigned>(type), static_cast<unsigned>(flags), reshade::log::iid_to_string(riid).c_str(), pp_command_list);
		const HRESULT hr = reshade::hooks::call_vtable<51, HRESULT>(device, node_mask, type, flags, riid, pp_command_list);
		if (gpmi_should_log(s_gpmi_raw_device_log_count))
			reshade::log::message(reshade::log::level::info, "[GPMI runtime] RAW D3D12 CreateCommandList1 result hr=%s raw=%p", reshade::log::hr_to_string(hr).c_str(), SUCCEEDED(hr) && pp_command_list != nullptr ? *pp_command_list : nullptr);
		if (SUCCEEDED(hr) && pp_command_list != nullptr && *pp_command_list != nullptr && type >= D3D12_COMMAND_LIST_TYPE_DIRECT && type <= D3D12_COMMAND_LIST_TYPE_COPY)
			gpmi_install_command_list_hooks(static_cast<ID3D12GraphicsCommandList *>(*pp_command_list));
		return hr;
	}

	void STDMETHODCALLTYPE gpmi_dev_CreateConstantBufferView(ID3D12Device *device, const D3D12_CONSTANT_BUFFER_VIEW_DESC *desc, D3D12_CPU_DESCRIPTOR_HANDLE dest)
	{
		if (gpmi_should_log(s_gpmi_raw_view_log_count))
			reshade::log::message(reshade::log::level::info, "[GPMI runtime] RAW D3D12 CreateConstantBufferView device=%p buffer=0x%llx size=%u dest_cpu=0x%llx", device, desc != nullptr ? static_cast<unsigned long long>(desc->BufferLocation) : 0ull, desc != nullptr ? desc->SizeInBytes : 0u, static_cast<unsigned long long>(dest.ptr));
		return reshade::hooks::call_vtable<17, void>(device, desc, dest);
	}

	void STDMETHODCALLTYPE gpmi_dev_CreateShaderResourceView(ID3D12Device *device, ID3D12Resource *resource, const D3D12_SHADER_RESOURCE_VIEW_DESC *desc, D3D12_CPU_DESCRIPTOR_HANDLE dest)
	{
		if (gpmi_should_log(s_gpmi_raw_view_log_count))
		{
			reshade::log::message(reshade::log::level::info, "[GPMI runtime] RAW D3D12 CreateShaderResourceView device=%p resource=%p srv_dim=%u srv_fmt=%u dest_cpu=0x%llx", device, resource, desc != nullptr ? static_cast<unsigned>(desc->ViewDimension) : 0u, desc != nullptr ? static_cast<unsigned>(desc->Format) : 0u, static_cast<unsigned long long>(dest.ptr));
			if (resource != nullptr) { const D3D12_RESOURCE_DESC rdesc = resource->GetDesc(); gpmi_log_resource_desc("RAW CreateShaderResourceView resource", &rdesc); }
		}
		return reshade::hooks::call_vtable<18, void>(device, resource, desc, dest);
	}

	HRESULT STDMETHODCALLTYPE gpmi_dev_TryCreateShaderResourceView(ID3D12Device15 *device, ID3D12Resource *resource, const D3D12_SHADER_RESOURCE_VIEW_DESC *desc, D3D12_CPU_DESCRIPTOR_HANDLE dest)
	{
		if (gpmi_should_log(s_gpmi_raw_view_log_count))
		{
			reshade::log::message(reshade::log::level::info, "[GPMI runtime] RAW D3D12 TryCreateShaderResourceView device=%p resource=%p srv_dim=%u srv_fmt=%u dest_cpu=0x%llx", device, resource, desc != nullptr ? static_cast<unsigned>(desc->ViewDimension) : 0u, desc != nullptr ? static_cast<unsigned>(desc->Format) : 0u, static_cast<unsigned long long>(dest.ptr));
			if (resource != nullptr) { const D3D12_RESOURCE_DESC rdesc = resource->GetDesc(); gpmi_log_resource_desc("RAW TryCreateShaderResourceView resource", &rdesc); }
		}
		return reshade::hooks::call_vtable<85, HRESULT>(device, resource, desc, dest);
	}

	void STDMETHODCALLTYPE gpmi_dev_CreateUnorderedAccessView(ID3D12Device *device, ID3D12Resource *resource, ID3D12Resource *counter, const D3D12_UNORDERED_ACCESS_VIEW_DESC *desc, D3D12_CPU_DESCRIPTOR_HANDLE dest)
	{
		if (gpmi_should_log(s_gpmi_raw_view_log_count))
		{
			reshade::log::message(reshade::log::level::info, "[GPMI runtime] RAW D3D12 CreateUnorderedAccessView device=%p resource=%p counter=%p uav_dim=%u uav_fmt=%u dest_cpu=0x%llx", device, resource, counter, desc != nullptr ? static_cast<unsigned>(desc->ViewDimension) : 0u, desc != nullptr ? static_cast<unsigned>(desc->Format) : 0u, static_cast<unsigned long long>(dest.ptr));
			if (resource != nullptr) { const D3D12_RESOURCE_DESC rdesc = resource->GetDesc(); gpmi_log_resource_desc("RAW CreateUnorderedAccessView resource", &rdesc); }
		}
		return reshade::hooks::call_vtable<19, void>(device, resource, counter, desc, dest);
	}

	void STDMETHODCALLTYPE gpmi_dev_CopyDescriptors(ID3D12Device *device, UINT num_dst, const D3D12_CPU_DESCRIPTOR_HANDLE *dst_starts, const UINT *dst_sizes, UINT num_src, const D3D12_CPU_DESCRIPTOR_HANDLE *src_starts, const UINT *src_sizes, D3D12_DESCRIPTOR_HEAP_TYPE type)
	{
		if (gpmi_should_log(s_gpmi_raw_view_log_count))
			reshade::log::message(reshade::log::level::info, "[GPMI runtime] RAW D3D12 CopyDescriptors device=%p dst_ranges=%u src_ranges=%u type=%u first_dst=0x%llx first_src=0x%llx", device, num_dst, num_src, static_cast<unsigned>(type), num_dst != 0 && dst_starts != nullptr ? static_cast<unsigned long long>(dst_starts[0].ptr) : 0ull, num_src != 0 && src_starts != nullptr ? static_cast<unsigned long long>(src_starts[0].ptr) : 0ull);
		return reshade::hooks::call_vtable<23, void>(device, num_dst, dst_starts, dst_sizes, num_src, src_starts, src_sizes, type);
	}

	void STDMETHODCALLTYPE gpmi_dev_CopyDescriptorsSimple(ID3D12Device *device, UINT num, D3D12_CPU_DESCRIPTOR_HANDLE dst, D3D12_CPU_DESCRIPTOR_HANDLE src, D3D12_DESCRIPTOR_HEAP_TYPE type)
	{
		if (gpmi_should_log(s_gpmi_raw_view_log_count))
			reshade::log::message(reshade::log::level::info, "[GPMI runtime] RAW D3D12 CopyDescriptorsSimple device=%p count=%u type=%u dst=0x%llx src=0x%llx", device, num, static_cast<unsigned>(type), static_cast<unsigned long long>(dst.ptr), static_cast<unsigned long long>(src.ptr));
		return reshade::hooks::call_vtable<24, void>(device, num, dst, src, type);
	}

	HRESULT STDMETHODCALLTYPE gpmi_dev_CreateCommittedResource(ID3D12Device *device, const D3D12_HEAP_PROPERTIES *heap_props, D3D12_HEAP_FLAGS heap_flags, const D3D12_RESOURCE_DESC *desc, D3D12_RESOURCE_STATES initial_state, const D3D12_CLEAR_VALUE *clear_value, REFIID riid, void **pp_resource)
	{
		if (gpmi_should_log(s_gpmi_raw_device_log_count))
		{
			reshade::log::message(reshade::log::level::info, "[GPMI runtime] RAW D3D12 CreateCommittedResource device=%p heap_type=%u heap_flags=0x%x initial_state=0x%x riid=%s pp=%p", device, heap_props != nullptr ? static_cast<unsigned>(heap_props->Type) : 0u, static_cast<unsigned>(heap_flags), static_cast<unsigned>(initial_state), reshade::log::iid_to_string(riid).c_str(), pp_resource);
			gpmi_log_resource_desc("RAW CreateCommittedResource", desc);
		}
		const HRESULT hr = reshade::hooks::call_vtable<27, HRESULT>(device, heap_props, heap_flags, desc, initial_state, clear_value, riid, pp_resource);
		if (SUCCEEDED(hr) && gpmi_should_log(s_gpmi_raw_device_log_count))
			reshade::log::message(reshade::log::level::info, "[GPMI runtime] RAW D3D12 CreateCommittedResource result hr=%s resource=%p", reshade::log::hr_to_string(hr).c_str(), pp_resource != nullptr ? *pp_resource : nullptr);
#if RESHADE_ADDON >= 2
		if (SUCCEEDED(hr) && pp_resource != nullptr && *pp_resource != nullptr)
			gpmi_install_resource_map_hooks(static_cast<ID3D12Resource *>(*pp_resource));
#endif
		return hr;
	}

	HRESULT STDMETHODCALLTYPE gpmi_dev_CreateCommittedResource1(ID3D12Device4 *device, const D3D12_HEAP_PROPERTIES *heap_props, D3D12_HEAP_FLAGS heap_flags, const D3D12_RESOURCE_DESC *desc, D3D12_RESOURCE_STATES initial_state, const D3D12_CLEAR_VALUE *clear_value, ID3D12ProtectedResourceSession *session, REFIID riid, void **pp_resource)
	{
		if (gpmi_should_log(s_gpmi_raw_device_log_count)) { reshade::log::message(reshade::log::level::info, "[GPMI runtime] RAW D3D12 CreateCommittedResource1 device=%p heap_type=%u initial_state=0x%x session=%p riid=%s", device, heap_props != nullptr ? static_cast<unsigned>(heap_props->Type) : 0u, static_cast<unsigned>(initial_state), session, reshade::log::iid_to_string(riid).c_str()); gpmi_log_resource_desc("RAW CreateCommittedResource1", desc); }
		return reshade::hooks::call_vtable<53, HRESULT>(device, heap_props, heap_flags, desc, initial_state, clear_value, session, riid, pp_resource);
	}

	HRESULT STDMETHODCALLTYPE gpmi_dev_CreateCommittedResource2(ID3D12Device8 *device, const D3D12_HEAP_PROPERTIES *heap_props, D3D12_HEAP_FLAGS heap_flags, const D3D12_RESOURCE_DESC1 *desc, D3D12_RESOURCE_STATES initial_state, const D3D12_CLEAR_VALUE *clear_value, ID3D12ProtectedResourceSession *session, REFIID riid, void **pp_resource)
	{
		if (gpmi_should_log(s_gpmi_raw_device_log_count)) { reshade::log::message(reshade::log::level::info, "[GPMI runtime] RAW D3D12 CreateCommittedResource2 device=%p heap_type=%u initial_state=0x%x session=%p riid=%s", device, heap_props != nullptr ? static_cast<unsigned>(heap_props->Type) : 0u, static_cast<unsigned>(initial_state), session, reshade::log::iid_to_string(riid).c_str()); gpmi_log_resource_desc1("RAW CreateCommittedResource2", desc); }
		return reshade::hooks::call_vtable<69, HRESULT>(device, heap_props, heap_flags, desc, initial_state, clear_value, session, riid, pp_resource);
	}

	HRESULT STDMETHODCALLTYPE gpmi_dev_CreateCommittedResource3(ID3D12Device10 *device, const D3D12_HEAP_PROPERTIES *heap_props, D3D12_HEAP_FLAGS heap_flags, const D3D12_RESOURCE_DESC1 *desc, D3D12_BARRIER_LAYOUT initial_layout, const D3D12_CLEAR_VALUE *clear_value, ID3D12ProtectedResourceSession *session, UINT32 num_castable_formats, const DXGI_FORMAT *castable_formats, REFIID riid, void **pp_resource)
	{
		if (gpmi_should_log(s_gpmi_raw_device_log_count)) { reshade::log::message(reshade::log::level::info, "[GPMI runtime] RAW D3D12 CreateCommittedResource3 device=%p heap_type=%u initial_layout=%u session=%p castable=%u riid=%s", device, heap_props != nullptr ? static_cast<unsigned>(heap_props->Type) : 0u, static_cast<unsigned>(initial_layout), session, num_castable_formats, reshade::log::iid_to_string(riid).c_str()); gpmi_log_resource_desc1("RAW CreateCommittedResource3", desc); }
		return reshade::hooks::call_vtable<76, HRESULT>(device, heap_props, heap_flags, desc, initial_layout, clear_value, session, num_castable_formats, castable_formats, riid, pp_resource);
	}

	HRESULT STDMETHODCALLTYPE gpmi_dev_CreatePlacedResource(ID3D12Device *device, ID3D12Heap *heap, UINT64 heap_offset, const D3D12_RESOURCE_DESC *desc, D3D12_RESOURCE_STATES initial_state, const D3D12_CLEAR_VALUE *clear_value, REFIID riid, void **pp_resource)
	{
		if (gpmi_should_log(s_gpmi_raw_device_log_count)) { reshade::log::message(reshade::log::level::info, "[GPMI runtime] RAW D3D12 CreatePlacedResource device=%p heap=%p offset=%llu initial_state=0x%x riid=%s pp=%p", device, heap, static_cast<unsigned long long>(heap_offset), static_cast<unsigned>(initial_state), reshade::log::iid_to_string(riid).c_str(), pp_resource); gpmi_log_resource_desc("RAW CreatePlacedResource", desc); }
		const HRESULT hr = reshade::hooks::call_vtable<29, HRESULT>(device, heap, heap_offset, desc, initial_state, clear_value, riid, pp_resource);
#if RESHADE_ADDON >= 2
		if (SUCCEEDED(hr) && pp_resource != nullptr && *pp_resource != nullptr)
			gpmi_install_resource_map_hooks(static_cast<ID3D12Resource *>(*pp_resource));
#endif
		return hr;
	}

	HRESULT STDMETHODCALLTYPE gpmi_dev_CreatePlacedResource1(ID3D12Device8 *device, ID3D12Heap *heap, UINT64 heap_offset, const D3D12_RESOURCE_DESC1 *desc, D3D12_RESOURCE_STATES initial_state, const D3D12_CLEAR_VALUE *clear_value, REFIID riid, void **pp_resource)
	{
		if (gpmi_should_log(s_gpmi_raw_device_log_count)) { reshade::log::message(reshade::log::level::info, "[GPMI runtime] RAW D3D12 CreatePlacedResource1 device=%p heap=%p offset=%llu initial_state=0x%x riid=%s", device, heap, static_cast<unsigned long long>(heap_offset), static_cast<unsigned>(initial_state), reshade::log::iid_to_string(riid).c_str()); gpmi_log_resource_desc1("RAW CreatePlacedResource1", desc); }
		return reshade::hooks::call_vtable<70, HRESULT>(device, heap, heap_offset, desc, initial_state, clear_value, riid, pp_resource);
	}

	HRESULT STDMETHODCALLTYPE gpmi_dev_CreatePlacedResource2(ID3D12Device10 *device, ID3D12Heap *heap, UINT64 heap_offset, const D3D12_RESOURCE_DESC1 *desc, D3D12_BARRIER_LAYOUT initial_layout, const D3D12_CLEAR_VALUE *clear_value, UINT32 num_castable_formats, const DXGI_FORMAT *castable_formats, REFIID riid, void **pp_resource)
	{
		if (gpmi_should_log(s_gpmi_raw_device_log_count)) { reshade::log::message(reshade::log::level::info, "[GPMI runtime] RAW D3D12 CreatePlacedResource2 device=%p heap=%p offset=%llu initial_layout=%u castable=%u riid=%s", device, heap, static_cast<unsigned long long>(heap_offset), static_cast<unsigned>(initial_layout), num_castable_formats, reshade::log::iid_to_string(riid).c_str()); gpmi_log_resource_desc1("RAW CreatePlacedResource2", desc); }
		return reshade::hooks::call_vtable<77, HRESULT>(device, heap, heap_offset, desc, initial_layout, clear_value, num_castable_formats, castable_formats, riid, pp_resource);
	}

	void gpmi_install_base_device_hooks(ID3D12Device *device)
	{
		if (device == nullptr)
			return;
		auto vtable = reshade::hooks::vtable_from_instance(device);
		reshade::hooks::install("GPMI ID3D12Device::CreateCommandList", vtable, 12, &gpmi_dev_CreateCommandList);
		reshade::hooks::install("GPMI ID3D12Device::CreateConstantBufferView", vtable, 17, &gpmi_dev_CreateConstantBufferView);
		reshade::hooks::install("GPMI ID3D12Device::CreateShaderResourceView", vtable, 18, &gpmi_dev_CreateShaderResourceView);
		reshade::hooks::install("GPMI ID3D12Device::CreateUnorderedAccessView", vtable, 19, &gpmi_dev_CreateUnorderedAccessView);
		reshade::hooks::install("GPMI ID3D12Device::CopyDescriptors", vtable, 23, &gpmi_dev_CopyDescriptors);
		reshade::hooks::install("GPMI ID3D12Device::CopyDescriptorsSimple", vtable, 24, &gpmi_dev_CopyDescriptorsSimple);
		reshade::hooks::install("GPMI ID3D12Device::CreateCommittedResource", vtable, 27, &gpmi_dev_CreateCommittedResource);
		reshade::hooks::install("GPMI ID3D12Device::CreatePlacedResource", vtable, 29, &gpmi_dev_CreatePlacedResource);
	}

	void gpmi_install_extended_proxy_device_hooks(ID3D12Device *device)
	{
		if (device == nullptr)
			return;
		auto vtable = reshade::hooks::vtable_from_instance(device);
		reshade::hooks::install("GPMI ID3D12Device4::CreateCommandList1", vtable, 51, &gpmi_dev_CreateCommandList1);
		reshade::hooks::install("GPMI ID3D12Device4::CreateCommittedResource1", vtable, 53, &gpmi_dev_CreateCommittedResource1);
		reshade::hooks::install("GPMI ID3D12Device8::CreateCommittedResource2", vtable, 69, &gpmi_dev_CreateCommittedResource2);
		reshade::hooks::install("GPMI ID3D12Device8::CreatePlacedResource1", vtable, 70, &gpmi_dev_CreatePlacedResource1);
		reshade::hooks::install("GPMI ID3D12Device10::CreateCommittedResource3", vtable, 76, &gpmi_dev_CreateCommittedResource3);
		reshade::hooks::install("GPMI ID3D12Device10::CreatePlacedResource2", vtable, 77, &gpmi_dev_CreatePlacedResource2);
		reshade::hooks::install("GPMI ID3D12Device15::TryCreateShaderResourceView", vtable, 85, &gpmi_dev_TryCreateShaderResourceView);
	}
}

extern "C" HRESULT WINAPI D3D12CreateDevice(IUnknown *pAdapter, D3D_FEATURE_LEVEL MinimumFeatureLevel, REFIID riid, void **ppDevice)
{
	const auto trampoline = reshade::hooks::call(D3D12CreateDevice);

	// Pass on unmodified in case this called from within 'Direct3DCreate9', which indicates that the D3D9 runtime is trying to create an internal device for D3D9on12, which should not be hooked
	if (g_in_dxgi_runtime)
		return trampoline(pAdapter, MinimumFeatureLevel, riid, ppDevice);

	// Need to lock during device creation to ensure an existing device proxy cannot be destroyed in while it is queried below
	const std::unique_lock<std::shared_mutex> lock(g_d3d12_adapter_mutex);

	reshade::log::message(
		reshade::log::level::info,
		"Redirecting D3D12CreateDevice(pAdapter = %p, MinimumFeatureLevel = %x, riid = %s, ppDevice = %p) ...",
		pAdapter, MinimumFeatureLevel, reshade::log::iid_to_string(riid).c_str(), ppDevice);

	com_ptr<DXGIAdapter> adapter_proxy;
	if (pAdapter && SUCCEEDED(pAdapter->QueryInterface(&adapter_proxy)))
		pAdapter = adapter_proxy->_orig;

#if RESHADE_ADDON >= 2
	if (ppDevice != nullptr)
	{
		reshade::load_addons();

		uint32_t api_version = static_cast<uint32_t>(MinimumFeatureLevel);
		if (reshade::invoke_addon_event<reshade::addon_event::create_device>(reshade::api::device_api::d3d12, api_version))
		{
			MinimumFeatureLevel = static_cast<D3D_FEATURE_LEVEL>(api_version);
		}
	}
#endif

	// NVIDIA Ansel creates a D3D11 device internally, so to avoid hooking that, set the flag that forces 'D3D11CreateDevice' to return early
	g_in_dxgi_runtime = true;
	const HRESULT hr = trampoline(pAdapter, MinimumFeatureLevel, riid, ppDevice);
	g_in_dxgi_runtime = false;

	// Skip calls that only check feature level support
	if (ppDevice == nullptr)
		return hr;

	if (FAILED(hr))
	{
#if RESHADE_ADDON >= 2
		reshade::unload_addons();
#endif

		reshade::log::message(reshade::log::level::warning, "D3D12CreateDevice failed with error code %s.", reshade::log::hr_to_string(hr).c_str());
		return hr;
	}

	// The returned device should alway implement the 'ID3D12Device' base interface
	const auto device = static_cast<ID3D12Device *>(*ppDevice);
	gpmi_install_base_device_hooks(device);

	// Direct3D 12 devices are singletons per adapter, so first check if one was already created previously
	D3D12Device *device_proxy = nullptr;
	D3D12Device *const device_proxy_existing = get_private_pointer_d3dx<D3D12Device>(device);
	if (device_proxy_existing != nullptr && device_proxy_existing->_orig == device)
	{
		InterlockedIncrement(&device_proxy_existing->_ref);
		device_proxy = device_proxy_existing;
	}
	else
	{
		device_proxy = new D3D12Device(device);
	}

	gpmi_install_base_device_hooks(static_cast<ID3D12Device *>(device_proxy));
	gpmi_install_extended_proxy_device_hooks(static_cast<ID3D12Device *>(device_proxy));

#if RESHADE_ADDON >= 2
	// Device proxy was created at this point, which increased the add-on manager reference count, so can release the reference added above again
	reshade::unload_addons();
#endif

	// Upgrade to the actual interface version requested here
	if (device_proxy->check_and_upgrade_interface(riid))
	{
#if RESHADE_VERBOSE_LOG
		reshade::log::message(
			reshade::log::level::debug,
			"Returning ID3D12Device%hu object %p (%p).",
			device_proxy->_interface_version, device_proxy, device_proxy->_orig);
#endif
		*ppDevice = device_proxy;
	}
	else // Do not hook object if we do not support the requested interface
	{
		reshade::log::message(reshade::log::level::warning, "Unknown interface %s in D3D12CreateDevice.", reshade::log::iid_to_string(riid).c_str());

		if (device_proxy != device_proxy_existing)
			delete device_proxy; // Delete instead of release to keep reference count untouched
	}

	return hr;
}

extern "C" HRESULT WINAPI D3D12GetDebugInterface(REFIID riid, void **ppvDebug)
{
	return reshade::hooks::call(D3D12GetDebugInterface)(riid, ppvDebug);
}

extern "C" HRESULT WINAPI D3D12CreateRootSignatureDeserializer(LPCVOID pSrcData, SIZE_T SrcDataSizeInBytes, REFIID pRootSignatureDeserializerInterface, void **ppRootSignatureDeserializer)
{
	return reshade::hooks::call(D3D12CreateRootSignatureDeserializer)(pSrcData, SrcDataSizeInBytes, pRootSignatureDeserializerInterface, ppRootSignatureDeserializer);
}

extern "C" HRESULT WINAPI D3D12CreateVersionedRootSignatureDeserializer(LPCVOID pSrcData, SIZE_T SrcDataSizeInBytes, REFIID pRootSignatureDeserializerInterface, void **ppRootSignatureDeserializer)
{
	return reshade::hooks::call(D3D12CreateVersionedRootSignatureDeserializer)(pSrcData, SrcDataSizeInBytes, pRootSignatureDeserializerInterface, ppRootSignatureDeserializer);
}

extern "C" HRESULT WINAPI D3D12EnableExperimentalFeatures(UINT NumFeatures, const IID *pIIDs, void *pConfigurationStructs, UINT *pConfigurationStructSizes)
{
	return reshade::hooks::call(D3D12EnableExperimentalFeatures)(NumFeatures, pIIDs, pConfigurationStructs, pConfigurationStructSizes);
}

extern "C" HRESULT WINAPI D3D12GetInterface(REFCLSID rclsid, REFIID riid, void **ppvDebug)
{
	return reshade::hooks::call(D3D12GetInterface)(rclsid, riid, ppvDebug);
}

extern "C" HRESULT WINAPI D3D12SerializeRootSignature(const D3D12_ROOT_SIGNATURE_DESC *pRootSignature, D3D_ROOT_SIGNATURE_VERSION Version, ID3DBlob **ppBlob, ID3DBlob **ppErrorBlob)
{
	return reshade::hooks::call(D3D12SerializeRootSignature)(pRootSignature, Version, ppBlob, ppErrorBlob);
}

extern "C" HRESULT WINAPI D3D12SerializeVersionedRootSignature(const D3D12_VERSIONED_ROOT_SIGNATURE_DESC *pRootSignature, ID3DBlob **ppBlob, ID3DBlob **ppErrorBlob)
{
	return reshade::hooks::call(D3D12SerializeVersionedRootSignature)(pRootSignature, ppBlob, ppErrorBlob);
}
