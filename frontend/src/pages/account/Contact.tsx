/**
 * My Account › Profile › Contact details: both email addresses -- each with
 * whether it is confirmed, and its link sent again -- the phones and the
 * address. Read first; *Edit* turns the same card into the form, saved at
 * once without review, and back. A new private address is the login and waits
 * to be confirmed; so does a new university address. The rules are the
 * server's (forms.py): a student's university address, for one, stays one.
 */
import { Button, Group, Select, SimpleGrid, Stack, Text, TextInput } from '@mantine/core';
import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';

import { api, ApiError, call, type Schemas } from '../../api/client';
import { Details } from '../../components/Details';
import { Panel } from '../../components/Panel';
import { emptyToNull } from '../../lib/forms';
import { notifyFailed } from '../../lib/notify';
import { AddressState } from './Emails';
import { type FormOptions, useTakeSaved } from './shared';

type Contact = Schemas['ContactOut'];
type ContactIn = Schemas['ContactIn'];
type Address = Schemas['EmailAddressOut'];

/** Whether the field still holds the saved address -- what its state is about. */
function same(value: string, saved: Address | null): saved is Address {
  return saved !== null && value.trim().toLowerCase() === saved.address.toLowerCase();
}

function initial(contact: Contact): ContactIn {
  return {
    street: contact.street ?? '',
    house_number: contact.house_number ?? '',
    postal_code: contact.postal_code ?? '',
    city: contact.city ?? '',
    country: contact.country ?? '',
    phone_private: contact.phone_private ?? '',
    email_private: contact.email_private ?? '',
    phone_work: contact.phone_work ?? '',
    email_work: contact.email_work ?? '',
  };
}

function address(contact: Contact) {
  const street = [contact.street, contact.house_number].filter(Boolean).join(' ');
  const town = [contact.postal_code, contact.city].filter(Boolean).join(' ');
  return [street, town, contact.country].filter(Boolean).join(', ') || null;
}

/** An address as it is saved, with whether it is confirmed and its link sent again. */
function Saved({ address: saved, which }: { address: Address; which: 'private' | 'work' }) {
  return (
    <Stack gap={6}>
      <Text size="sm" style={{ overflowWrap: 'anywhere' }}>
        {saved.address}
      </Text>
      <AddressState address={saved} which={which} />
    </Stack>
  );
}

export function ContactCard({
  contact,
  options,
  email,
  workEmail,
}: {
  contact: Contact;
  options: FormOptions;
  /** The saved addresses, with whether each is confirmed. */
  email: Address;
  workEmail: Address | null;
}) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(() => initial(contact));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const take = useTakeSaved();
  const save = useMutation({
    mutationFn: () =>
      call(
        api.PUT('/api/v1/account/contact', {
          body: {
            ...value,
            phone_work: emptyToNull(value.phone_work),
            email_work: emptyToNull(value.email_work),
          },
        }),
      ),
    onSuccess: (saved) => {
      setErrors({});
      setEditing(false);
      take(saved);
    },
    onError: (error) => {
      if (error instanceof ApiError && Object.keys(error.fields).length) setErrors(error.fields);
      else notifyFailed(error);
    },
  });
  const field = (key: keyof ContactIn) => ({
    value: value[key] ?? '',
    error: errors[key] ?? null,
    onChange: (event: React.ChangeEvent<HTMLInputElement>) => {
      setValue({ ...value, [key]: event.currentTarget.value });
      if (errors[key]) setErrors({ ...errors, [key]: '' });
    },
  });

  if (!editing)
    return (
      <Panel
        title="Contact details"
        actions={
          <Button
            size="xs"
            variant="default"
            onClick={() => {
              setValue(initial(contact));
              setEditing(true);
            }}
          >
            Edit
          </Button>
        }
      >
        <Details
          items={[
            ['Private email', <Saved key="private" address={email} which="private" />],
            [
              'University or company email',
              workEmail ? <Saved key="work" address={workEmail} which="work" /> : null,
            ],
            ['Private phone', contact.phone_private],
            ['Work phone', contact.phone_work],
            ['Address', address(contact)],
          ]}
        />
      </Panel>
    );

  return (
    <Panel title="Contact details">
      <form
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate();
        }}
      >
        <Stack gap="md">
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
            <Stack gap={6}>
              <TextInput
                label="Private email"
                description="Your login. A new one waits to be confirmed."
                type="email"
                autoComplete="email"
                maxLength={255}
                {...field('email_private')}
              />
              {same(value.email_private, email) ? <AddressState address={email} which="private" /> : null}
            </Stack>
            <Stack gap={6}>
              <TextInput
                label="University or company email"
                description="A new one waits to be confirmed."
                type="email"
                maxLength={255}
                {...field('email_work')}
              />
              {same(value.email_work ?? '', workEmail) ? (
                <AddressState address={workEmail} which="work" />
              ) : null}
            </Stack>
            <TextInput
              label="Private phone"
              type="tel"
              autoComplete="tel"
              maxLength={50}
              {...field('phone_private')}
            />
            <TextInput label="Work phone" type="tel" maxLength={50} {...field('phone_work')} />
          </SimpleGrid>
          <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="md">
            <TextInput label="Street" autoComplete="address-line1" maxLength={255} {...field('street')} />
            <TextInput label="House number" maxLength={20} {...field('house_number')} />
            <TextInput
              label="Postal code"
              autoComplete="postal-code"
              maxLength={20}
              {...field('postal_code')}
            />
            <TextInput label="City" autoComplete="address-level2" maxLength={100} {...field('city')} />
            <Select
              label="Country"
              data={options.countries}
              searchable
              value={value.country || null}
              error={errors.country ?? null}
              onChange={(country) => {
                setValue({ ...value, country: country ?? '' });
              }}
            />
          </SimpleGrid>
          <Group justify="flex-end">
            <Button
              variant="default"
              onClick={() => {
                setErrors({});
                setEditing(false);
              }}
            >
              Cancel
            </Button>
            <Button type="submit" loading={save.isPending}>
              Save
            </Button>
          </Group>
        </Stack>
      </form>
    </Panel>
  );
}
