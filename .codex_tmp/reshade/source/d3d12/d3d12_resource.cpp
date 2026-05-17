/*
 * Copyright (C) 2022 Patrick Mours
 * SPDX-License-Identifier: BSD-3-Clause
 */

#include "d3d12_device.hpp"
#include "d3d12_resource.hpp"
#include "com_utils.hpp"
#include "hook_manager.hpp"
#include "dll_log.hpp"

#include <atomic>

extern std::shared_mutex g_d3d12_adapter_mutex;

// Monster Hunter Rise calls 'ID3D12Device::CopyDescriptorsSimple' on a device queried from a resource
// This crashes if that device pointer is not pointing to the proxy device, due to our modified descriptor handles, so need to make sure that is the case
HRESULT STDMETHODCALLTYPE ID3D12Resource_GetDevice(ID3D12Resource *pResource, REFIID riid, void **ppvDevice)
{
	const HRESULT hr = reshade::hooks::call(ID3D12Resource_GetDevice, reshade::hooks::vtable_from_instance(pResource) + 7)(pResource, riid, ppvDevice);
	if (FAILED(hr))
		return hr;

	const auto device = static_cast<ID3D12Device *>(*ppvDevice);
	assert(device != nullptr);

	const std::unique_lock<std::shared_mutex> lock(g_d3d12_adapter_mutex);

	const auto device_proxy = get_private_pointer_d3dx<D3D12Device>(device);
	if (device_proxy != nullptr && device_proxy->_orig == device)
	{
		InterlockedIncrement(&device_proxy->_ref);
		*ppvDevice = device_proxy;
	}

	return hr;
}

#if RESHADE_ADDON >= 2

#include "d3d12_impl_type_convert.hpp"
#include "addon_manager.hpp"

using reshade::d3d12::to_handle;

namespace
{
	constexpr uint32_t GPMI_RESOURCE_LOG_LIMIT = 20000;
	std::atomic_uint32_t s_gpmi_resource_log_count = 0;

	bool gpmi_should_log_resource()
	{
		return s_gpmi_resource_log_count.fetch_add(1, std::memory_order_relaxed) < GPMI_RESOURCE_LOG_LIMIT;
	}

	void gpmi_log_resource_map_desc(const char *prefix, ID3D12Resource *resource, UINT subresource, const D3D12_RANGE *range, const void *data, HRESULT hr)
	{
		if (!gpmi_should_log_resource())
			return;

		if (resource == nullptr)
		{
			reshade::log::message(reshade::log::level::info, "[GPMI runtime] %s resource=null sub=%u hr=%s data=%p", prefix, subresource, reshade::log::hr_to_string(hr).c_str(), data);
			return;
		}

		const D3D12_RESOURCE_DESC desc = resource->GetDesc();
		reshade::log::message(
			reshade::log::level::info,
			"[GPMI runtime] %s resource=%p sub=%u hr=%s data=%p range=%llu-%llu dim=%u size=%llux%u depth_or_array=%u mips=%u fmt=%u layout=%u flags=0x%x",
			prefix,
			resource,
			subresource,
			reshade::log::hr_to_string(hr).c_str(),
			data,
			range != nullptr ? static_cast<unsigned long long>(range->Begin) : 0ull,
			range != nullptr ? static_cast<unsigned long long>(range->End) : 0ull,
			static_cast<unsigned>(desc.Dimension),
			static_cast<unsigned long long>(desc.Width),
			desc.Height,
			desc.DepthOrArraySize,
			desc.MipLevels,
			static_cast<unsigned>(desc.Format),
			static_cast<unsigned>(desc.Layout),
			static_cast<unsigned>(desc.Flags));
	}
}

HRESULT STDMETHODCALLTYPE ID3D12Resource_Map(ID3D12Resource *pResource, UINT Subresource, const D3D12_RANGE *pReadRange, void **ppData)
{
	const HRESULT hr = reshade::hooks::call(ID3D12Resource_Map, reshade::hooks::vtable_from_instance(pResource) + 8)(pResource, Subresource, pReadRange, ppData);
	gpmi_log_resource_map_desc("RAW D3D12 Resource::Map", pResource, Subresource, pReadRange, ppData != nullptr ? *ppData : nullptr, hr);
	if (FAILED(hr))
		return hr;

	com_ptr<ID3D12Device> device;
	pResource->GetDevice(IID_PPV_ARGS(&device));
	assert(device != nullptr);

	const auto device_proxy = get_private_pointer_d3dx<D3D12Device>(device.get());
	if (device_proxy != nullptr)
	{
		const D3D12_RESOURCE_DESC desc = pResource->GetDesc();

		if (desc.Dimension == D3D12_RESOURCE_DIMENSION_BUFFER)
		{
			assert(Subresource == 0);

			reshade::invoke_addon_event<reshade::addon_event::map_buffer_region>(
				device_proxy,
				to_handle(pResource),
				0,
				std::numeric_limits<uint64_t>::max(),
				pReadRange != nullptr && pReadRange->End <= pReadRange->Begin ? reshade::api::map_access::write_only : reshade::api::map_access::read_write,
				ppData);
		}
		else if (ppData != nullptr)
		{
			reshade::api::subresource_data data;
			data.data = *ppData;

			D3D12_PLACED_SUBRESOURCE_FOOTPRINT placed_footprint;
			device->GetCopyableFootprints(&desc, Subresource, 1, 0, &placed_footprint, &data.slice_pitch, nullptr, nullptr);

			data.row_pitch = placed_footprint.Footprint.RowPitch;
			data.slice_pitch *= placed_footprint.Footprint.RowPitch;

			reshade::invoke_addon_event<reshade::addon_event::map_texture_region>(
				device_proxy,
				to_handle(pResource),
				Subresource,
				nullptr,
				pReadRange != nullptr && pReadRange->End <= pReadRange->Begin ? reshade::api::map_access::write_only : reshade::api::map_access::read_write,
				&data);

			*ppData = data.data;
		}
		else
		{
			reshade::invoke_addon_event<reshade::addon_event::map_texture_region>(
				device_proxy,
				to_handle(pResource),
				Subresource,
				nullptr,
				pReadRange != nullptr && pReadRange->End <= pReadRange->Begin ? reshade::api::map_access::write_only : reshade::api::map_access::read_write,
				nullptr);
		}
	}

	return hr;
}
HRESULT STDMETHODCALLTYPE ID3D12Resource_Unmap(ID3D12Resource *pResource, UINT Subresource, const D3D12_RANGE *pWrittenRange)
{
	gpmi_log_resource_map_desc("RAW D3D12 Resource::Unmap", pResource, Subresource, pWrittenRange, nullptr, S_OK);

	com_ptr<ID3D12Device> device;
	pResource->GetDevice(IID_PPV_ARGS(&device));
	assert(device != nullptr);

	const auto device_proxy = get_private_pointer_d3dx<D3D12Device>(device.get());
	if (device_proxy != nullptr)
	{
		const D3D12_RESOURCE_DESC desc = pResource->GetDesc();

		if (desc.Dimension == D3D12_RESOURCE_DIMENSION_BUFFER)
		{
			assert(Subresource == 0);

			reshade::invoke_addon_event<reshade::addon_event::unmap_buffer_region>(device_proxy, to_handle(pResource));
		}
		else
		{
			reshade::invoke_addon_event<reshade::addon_event::unmap_texture_region>(device_proxy, to_handle(pResource), Subresource);
		}
	}

	return reshade::hooks::call(ID3D12Resource_Unmap, reshade::hooks::vtable_from_instance(pResource) + 9)(pResource, Subresource, pWrittenRange);
}

#endif
